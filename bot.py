import os
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

# Optional: tiny health server for Render (useful for monitoring)
# If you don't need it, set ENABLE_HEALTHCHECK=0
from aiohttp import web


# ---------------------- CONFIG ----------------------
@dataclass(frozen=True)
class Config:
    token: str
    admin_id: int
    channel_id: int
    db_path: str
    sub_days: int
    pay_ru: str
    pay_paypal: str
    tz: timezone
    enable_healthcheck: bool
    port: int


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    admin_id = int(os.getenv("ADMIN_ID", "0"))
    channel_id = int(os.getenv("CHANNEL_ID", "0"))
    if admin_id == 0 or channel_id == 0:
        raise RuntimeError("ADMIN_ID and CHANNEL_ID must be set")

    db_path = os.getenv("DB_PATH", "subscriptions.db").strip()
    sub_days = int(os.getenv("SUB_DAYS", "30"))

    pay_ru = os.getenv("PAY_RU", "2204120115044840")
    pay_paypal = os.getenv("PAY_PAYPAL", "neo832002@yahoi.com")

    # store times in UTC by default
    tz = timezone.utc

    enable_healthcheck = os.getenv("ENABLE_HEALTHCHECK", "1") not in ("0", "false", "False")
    port = int(os.getenv("PORT", "10000"))  # Render typically provides PORT for web services

    return Config(
        token=token,
        admin_id=admin_id,
        channel_id=channel_id,
        db_path=db_path,
        sub_days=sub_days,
        pay_ru=pay_ru,
        pay_paypal=pay_paypal,
        tz=tz,
        enable_healthcheck=enable_healthcheck,
        port=port,
    )


CFG = load_config()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("sub-bot")


bot = Bot(token=CFG.token)
dp = Dispatcher()


# ---------------------- DB LAYER (aiosqlite) ----------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS subs (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    expire_date TEXT
)
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_expire_date ON subs (expire_date)
"""


def _now() -> datetime:
    return datetime.now(CFG.tz)


def _dt_to_str(dt: datetime) -> str:
    # ISO format with timezone (safe parsing)
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    # datetime.fromisoformat supports timezone info if present
    return datetime.fromisoformat(s)


async def init_db() -> None:
    async with aiosqlite.connect(CFG.db_path) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.execute(CREATE_INDEX_SQL)
        await db.commit()
    log.info("DB initialized at %s", CFG.db_path)


async def upsert_sub(user_id: int, username: str | None, full_name: str | None) -> datetime:
    expire = _now() + timedelta(days=CFG.sub_days)
    async with aiosqlite.connect(CFG.db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO subs(user_id, username, full_name, expire_date) VALUES (?, ?, ?, ?)",
            (user_id, username or "", full_name or "", _dt_to_str(expire)),
        )
        await db.commit()
    return expire


async def get_sub(user_id: int):
    async with aiosqlite.connect(CFG.db_path) as db:
        cur = await db.execute("SELECT user_id, username, full_name, expire_date FROM subs WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        await cur.close()
    return row


async def is_sub_active(user_id: int) -> bool:
    row = await get_sub(user_id)
    if not row:
        return False
    expire_date = _str_to_dt(row[3])
    return expire_date > _now()


async def cleanup_expired() -> int:
    """Delete expired subs. Returns number of deleted rows."""
    async with aiosqlite.connect(CFG.db_path) as db:
        cur = await db.execute("SELECT user_id, expire_date FROM subs")
        rows = await cur.fetchall()
        await cur.close()

        expired_ids = []
        now = _now()
        for user_id, expire_s in rows:
            try:
                if _str_to_dt(expire_s) <= now:
                    expired_ids.append(user_id)
            except Exception:
                # corrupted date => treat as expired
                expired_ids.append(user_id)

        if not expired_ids:
            return 0

        await db.executemany("DELETE FROM subs WHERE user_id = ?", [(uid,) for uid in expired_ids])
        await db.commit()
        return len(expired_ids)


async def stats_counts() -> dict:
    now = _now()
    async with aiosqlite.connect(CFG.db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM subs")
        total = (await cur.fetchone())[0]
        await cur.close()

        # Active count: fetch all and compare in python (ok for small/medium).
        # For big scale use SQL datetime functions and normalized format.
        cur = await db.execute("SELECT expire_date FROM subs")
        rows = await cur.fetchall()
        await cur.close()

    active = 0
    for (expire_s,) in rows:
        try:
            if _str_to_dt(expire_s) > now:
                active += 1
        except Exception:
            pass

    return {"total": total, "active": active, "expired": total - active}


# ---------------------- UI HELPERS ----------------------
def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🧹 Очистить просроченные", callback_data="admin_cleanup")],
        ]
    )


def user_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
            [InlineKeyboardButton(text="🔎 Проверить подписку", callback_data="check_sub")],
        ]
    )


def admin_decision_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{user_id}"),
                InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{user_id}"),
            ]
        ]
    )


# ---------------------- HANDLERS ----------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        await message.answer("Админ-панель:", reply_markup=admin_kb())
        return

    await message.answer(
        "Привет! Оплати подписку и пришли скриншот чека.\n"
        "После подтверждения оплаты бот даст ссылку на вступление в канал.",
        reply_markup=user_start_kb(),
    )


@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    text = (
        f"🇷🇺 РФ: <code>{CFG.pay_ru}</code>\n"
        f"🌎 PayPal: <code>{CFG.pay_paypal}</code>\n\n"
        "После оплаты пришли сюда скриншот чека (фото)."
    )
    await callback.message.answer(text)
    await callback.answer()


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    active = await is_sub_active(callback.from_user.id)
    if not active:
        await callback.message.answer("Подписка не активна или отсутствует. Оплати и пришли чек.")
    else:
        row = await get_sub(callback.from_user.id)
        exp = _str_to_dt(row[3]).astimezone(CFG.tz)
        await callback.message.answer(f"✅ Подписка активна до: <code>{_dt_to_str(exp)}</code>")
    await callback.answer()


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """User sends payment screenshot -> forward to admin with inline decision buttons."""
    nick = f"@{message.from_user.username}" if message.from_user.username else "нет"
    caption = (
        f"Чек от: {message.from_user.full_name}\n"
        f"Ник: {nick}\n"
        f"ID: {message.from_user.id}\n"
        f"Дата: {_dt_to_str(_now())}"
    )
    try:
        await bot.send_photo(
            CFG.admin_id,
            message.photo[-1].file_id,
            caption=caption,
            reply_markup=admin_decision_kb(message.from_user.id),
        )
        await message.answer("⏳ Чек передан админу. Ожидай подтверждения.")
    except TelegramAPIError as e:
        log.exception("Failed to send photo to admin: %s", e)
        await message.answer("Не удалось отправить чек админу. Попробуй позже.")


@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    """Admin approves/declines payment."""
    if callback.from_user.id != CFG.admin_id:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    parts = callback.data.split("_", maxsplit=1)
    action = parts[0]
    try:
        uid = int(parts[1])
    except Exception:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    # Try to parse caption reliably (do not depend too hard on line order)
    caption = (callback.message.caption or "")
    # Fallback values
    name = ""
    nick = ""
    for line in caption.splitlines():
        if line.startswith("Чек от: "):
            name = line.replace("Чек от: ", "").strip()
        if line.startswith("Ник: "):
            nick = line.replace("Ник: ", "").strip()

    if action == "ok":
        try:
            # Create invite link with join request (most reliable for moderation flows)
            invite = await bot.create_chat_invite_link(
                CFG.channel_id,
                creates_join_request=True,
                name=f"sub-{uid}-{int(_now().timestamp())}",
            )
        except TelegramAPIError as e:
            log.exception("Failed to create invite link: %s", e)
            await callback.answer("Не удалось создать ссылку. Проверь права бота.", show_alert=True)
            return

        expire = await upsert_sub(uid, nick, name)

        try:
            await bot.send_message(
                uid,
                "✅ Оплата подтверждена!\n"
                f"Подай заявку по ссылке:\n{invite.invite_link}\n\n"
                "Бот одобрит её автоматически.\n"
                f"Подписка активна до: <code>{_dt_to_str(expire)}</code>",
            )
        except TelegramAPIError as e:
            log.exception("Failed to notify user %s: %s", uid, e)

        try:
            await callback.message.edit_caption(caption=caption + "\n\n✅ ОДОБРЕНО")
        except TelegramAPIError:
            pass

        await callback.answer("Одобрено.")
        return

    # action == "no"
    try:
        await bot.send_message(uid, "❌ Оплата отклонена. Если это ошибка — отправь чек повторно или напиши админу.")
    except TelegramAPIError as e:
        log.exception("Failed to notify user %s: %s", uid, e)

    try:
        await callback.message.edit_caption(caption=caption + "\n\n❌ ОТКЛОНЕНО")
    except TelegramAPIError:
        pass

    await callback.answer("Отклонено.")


@dp.chat_join_request()
async def approve_request(update: types.ChatJoinRequest):
    """Auto-approve join requests for users with active subscription."""
    try:
        if await is_sub_active(update.from_user.id):
            await update.approve()
        else:
            await update.decline()
    except TelegramAPIError as e:
        log.exception("Join request handling failed: %s", e)


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    counts = await stats_counts()
    await callback.message.answer(
        "📊 Статистика подписок:\n"
        f"Всего записей: <b>{counts['total']}</b>\n"
        f"Активные: <b>{counts['active']}</b>\n"
        f"Просроченные: <b>{counts['expired']}</b>\n"
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_cleanup")
async def admin_cleanup(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    deleted = await cleanup_expired()
    await callback.message.answer(f"🧹 Удалено просроченных подписок: <b>{deleted}</b>")
    await callback.answer()


# ---------------------- BACKGROUND TASKS ----------------------
async def periodic_cleanup_task():
    while True:
        try:
            deleted = await cleanup_expired()
            if deleted:
                log.info("Periodic cleanup removed %d expired subscriptions", deleted)
        except Exception:
            log.exception("Periodic cleanup error")
        # every 6 hours
        await asyncio.sleep(6 * 60 * 60)


# ---------------------- HEALTHCHECK SERVER ----------------------
async def health_app():
    app = web.Application()

    async def health(_request):
        return web.json_response({"ok": True, "service": "telegram-sub-bot", "time": _dt_to_str(_now())})

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    return app


async def run_health_server():
    app = await health_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=CFG.port)
    await site.start()
    log.info("Healthcheck server running on 0.0.0.0:%s", CFG.port)


# ---------------------- MAIN ----------------------
async def main():
    await init_db()

    # Start background cleanup
    asyncio.create_task(periodic_cleanup_task())

    # Optional health server (useful for Render web service)
    if CFG.enable_healthcheck:
        await run_health_server()

    log.info("Bot started (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

