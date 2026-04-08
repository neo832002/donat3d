import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite
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
    db_path: str = "subscriptions.db"
    sub_days: int = 30
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"  # Исправлено здесь
    tz: timezone = timezone.utc
    enable_healthcheck: bool = False
    port: int = 10000


CFG = Config()

logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("sub-bot")


bot = Bot(token=CFG.token)
dp = Dispatcher()


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
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
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
