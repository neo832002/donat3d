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
    pay_paypal: str = "neo832002@yahoo.com"
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
