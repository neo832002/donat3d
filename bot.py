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
    # Ссылка из настроек Render
    db_url: str = os.getenv("DATABASE_URL")
    sub_days: int = 30
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"
    tz: timezone = timezone.utc
    port: int = int(os.getenv("PORT", 10000))

CFG = Config()

logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sub-bot")

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- РАБОТА С БАЗОЙ (SUPABASE / POSTGRES) ---

async def get_conn():
    return await asyncpg.connect(CFG.db_url)

async def init_db():
    conn = await get_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS subs (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            expire_date TIMESTAMPTZ
        )
    """)
    await conn.close()
    log.info("DB initialized (Supabase)")

async def upsert_sub(user_id: int, username: str | None, full_name: str | None):
    expire = datetime.now(CFG.tz) + timedelta(days=CFG.sub_days)
    conn = await get_conn()
    await conn.execute("""
        INSERT INTO subs(user_id, username, full_name, expire_date) 
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE 
        SET username = $2, full_name = $3, expire_date = $4
    """, user_id, username or "", full_name or "", expire)
    await conn.close()
    return expire

async def get_sub(user_id: int):
    conn = await get_conn()
    row = await conn.fetchrow("SELECT * FROM subs WHERE user_id = $1", user_id)
    await conn.close()
    return row

async def is_sub_active(user_id: int) -> bool:
    row = await get_sub(user_id)
    if not row: return False
    return row['expire_date'] > datetime.now(CFG.tz)

# --- КЛАВИАТУРЫ ---

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])

def user_start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить подписку", callback_data="check_sub")]
    ])

def admin_decision_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{user_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{user_id}")
    ]])

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        await message.answer("Админ-панель:", reply_markup=admin_kb())
        return
    await message.answer("Привет! Оплати подписку и пришли скриншот чека.", reply_markup=user_start_kb())

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    text = f"🇷🇺 РФ: <code>{CFG.pay_ru}</code>\n🌎 PayPal: <code>{CFG.pay_paypal}</code>\n\nПришли фото чека."
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    active = await is_sub_active(callback.from_user.id)
    if not active:
        await callback.message.answer("Подписка не активна.")
    else:
        row = await get_sub(callback.from_user.id)
        date_str = row['expire_date'].strftime("%d.%m.%Y %H:%M")
        await callback.message.answer(f"✅ Активна до: {date_str}")
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await bot.send_photo(
        CFG.admin_id,
        message.photo[-1].file_id,
        caption=f"Чек от: {message.from_user.full_name}\nID: {message.from_user.id}",
        reply_markup=admin_decision_kb(message.from_user.id)
    )
    await message.answer("⏳ Чек передан админу.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    
    action, uid_str = callback.data.split("_")
    uid = int(uid_str)

    if action == "ok":
        await upsert_sub(uid, "User", "Name")
        try:
            invite = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
            await bot.send_message(uid, f"✅ Одобрено! Ваша ссылка: {invite.invite_link}")
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ ОДОБРЕНО")
        except Exception as e:
            log.error(f"Invite error: {e}")
    else:
        await bot.send_message(uid, "❌ Ваша оплата не подтверждена.")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n❌ ОТКАЗАНО")
    await callback.answer()

# --- ЗАПУСК ДЛЯ RENDER ---

async def handle_health(request):
    return web.Response(text="OK")

async def main():
    await init_db()
    # Настройка веб-сервера для Health Check
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", CFG.port)
    await site.start()
    
    log.info(f"Bot started on port {CFG.port}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
