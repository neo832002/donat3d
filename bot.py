import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError
from aiohttp import web


@dataclass(frozen=True)
class Config:
    token: str = "8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ"
    admin_id: int = 942900279
    channel_id: int = -1003581309063
    db_url: str = os.getenv("DATABASE_URL")  # Обязательно задайте переменную окружения DATABASE_URL
    sub_days: int = 30
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"
    tz: timezone = timezone.utc
    port: int = int(os.getenv("PORT", 10000))


CFG = Config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sub-bot")

bot = Bot(token=CFG.token)
dp = Dispatcher()


# --- Работа с базой (PostgreSQL через asyncpg) ---


async def get_conn():
    return await asyncpg.connect(CFG.db_url)


async def init_db():
    conn = await get_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subs (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            expire_date TIMESTAMPTZ
        )
        """
    )
    await conn.close()
    log.info("DB initialized (Supabase/Postgres)")


async def upsert_sub(user_id: int, username: str | None, full_name: str | None):
    expire = datetime.now(CFG.tz) + timedelta(days=CFG.sub_days)
    conn = await get_conn()
    await conn.execute(
        """
        INSERT INTO subs(user_id, username, full_name, expire_date) 
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE 
        SET username = $2, full_name = $3, expire_date = $4
        """,
        user_id,
        username or "",
        full_name or "",
        expire,
    )
    await conn.close()
    return expire


async def get_sub(user_id: int):
    conn = await get_conn()
    row = await conn.fetchrow("SELECT * FROM subs WHERE user_id = $1", user_id)
    await conn.close()
    return row


async def is_sub_active(user_id: int) -> bool:
    row = await get_sub(user_id)
    if not row:
        return False
    return row["expire_date"] > datetime.now(CFG.tz)


# --- Клавиатуры ---


def admin_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
        ]
    )


def user_start_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
            [InlineKeyboardButton(text="🔎 Проверить подписку", callback_data="check_sub")],
        ]
    )


def admin_decision_kb(user_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{user_id}"),
                InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{user_id}"),
            ]
        ]
    )


# --- Хендлеры ---


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        await message.answer("Админ-панель:", reply_markup=admin_kb())
        return
    await message.answer(
        "Привет! Оплати подписку и пришли скриншот чека.", reply_markup=user_start_kb()
    )


@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    text = (
        f"🇷🇺 РФ: <code>{CFG.pay_ru}</code>\n"
        f"🌎 PayPal: <code>{CFG.pay_paypal}</code>\n\n"
        "Пришли фото чека."
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    active = await is_sub_active(callback.from_user.id)
    if not active:
        await callback.message.answer("Подписка не активна.")
    else:
        row = await get_sub(callback.from_user.id)
        date_str = row["expire_date"].strftime("%d.%m.%Y %H:%M")
        await callback.message.answer(f"✅ Активна до: {date_str}")
    await callback.answer()


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    try:
        await bot.send_photo(
            CFG.admin_id,
            message.photo[-1].file_id,
            caption=f"Чек от: {message.from_user.full_name}\nID: {message.from_user.id}",
            reply_markup=admin_decision_kb(message.from_user.id),
        )
        await message.answer("⏳ Чек передан админу.")
    except TelegramAPIError as e:
        log.error(f"Failed to send photo to admin: {e}")
        await message.answer("Не удалось отправить чек админу. Попробуйте позже.")


@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id:
        await callback.answer("Недостаточно прав.")
        return

    action, uid_str = callback.data.split("_")
    uid = int(uid_str)

    if action == "ok":
        user = await get_sub(uid)
        username = user["username"] if user else ""
        full_name = user["full_name"] if user else ""

        expire = await upsert_sub(uid, username, full_name)
        try:
            invite = await bot.create_chat_invite_link(
                CFG.channel_id, member_limit=1, creates_join_request=True
            )
            await bot.send_message(
                uid,
                f"✅ Одобрено! Ваша ссылка для вступления:\n{invite.invite_link}\n"
                f"Подписка активна до: {expire.strftime('%d.%m.%Y %H:%M')}",
            )
            await callback.message.edit_caption(
                caption=f"{callback.message.caption}\n\n✅ ОДОБРЕНО"
            )
        except TelegramAPIError as e:
            log.error(f"Invite error: {e}")
            await callback.answer("Ошибка при создании ссылки. Проверьте права бота.")
            return
    else:
        await bot.send_message(uid, "❌ Ваша оплата не подтверждена.")
        await callback.message.edit_caption(
            caption=f"{callback.message.caption}\n\n❌ ОТКАЗАНО"
        )

    await callback.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    now = datetime.now(CFG.tz)
    conn = await get_conn()
    rows = await conn.fetch("SELECT user_id, username, full_name, expire_date FROM subs")
    await conn.close()

    active_subs = []
    for row in rows:
        if row["expire_date"] > now:
            active_subs.append(row)

    if not active_subs:
        await callback.message.answer("Нет активных подписчиков.")
        await callback.answer()
        return

    for sub in active_subs:
        name_display = sub["full_name"] if sub["full_name"] else "(без имени)"
        nick_display = f"@{sub['username']}" if sub["username"] else "(без ника)"
        expire_display = sub["expire_date"].strftime("%d.%m.%Y %H:%M")

        text = (
            f"👤 <b>{name_display}</b>\n"
            f"🔹 Ник: {nick_display}\n"
            f"🆔 ID: <code>{sub['user_id']}</code>\n"
            f"⏳ Подписка активна до: <code>{expire_display}</code>"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Отменить подписку",
                        callback_data=f"cancel_sub_{sub['user_id']}",
                    )
                ]
            ]
        )

        await callback.message.answer(text, reply_markup=kb)

    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_sub_"))
async def cancel_subscription(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    user_id_str = callback.data.removeprefix("cancel_sub_")
    try:
        user_id = int(user_id_str)
    except ValueError:
        await callback.answer("Некорректный ID пользователя.", show_alert=True)
        return

    conn = await get_conn()
    await conn.execute("DELETE FROM subs WHERE user_id = $1", user_id)
    await conn.close()

    await callback.answer(f"Подписка пользователя {user_id} отменена.")
    await callback.message.edit_text(f"Подписка пользователя {user_id} была отменена администратором.")


# --- Healthcheck для Render ---


async def handle_health(request):
    return web.Response(text="OK")


async def main():
    await init_db()

    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", CFG.port)
    await site.start()

    log.info(f"Bot started on port {CFG.port}")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import traceback

    try:
        asyncio.run(main())
    except Exception as e:
        print("Fatal error:", e)
        traceback.print_exc()
        raise

