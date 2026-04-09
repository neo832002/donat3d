import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web # Добавлено для Render

@dataclass(frozen=True)
class Config:
    token: str = "8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ"
    admin_id: int = 942900279
    channel_id: int = -1003581309063
    db_url: str = os.getenv("MONGODB_URI")
    sub_days: int = 30
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"
    tz: timezone = timezone.utc
    port: int = int(os.getenv("PORT", 10000)) # Порт для Render

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

# --- База данных (ИСПРАВЛЕНО) ---
client = AsyncIOMotorClient(CFG.db_url)
# Явно указываем имя базы данных "sub_bot_db"
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Веб-сервер для Render (Чтобы не было ошибки 500) ---
async def handle_render_healthcheck(request):
    return web.Response(text="Bot is alive")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_render_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", CFG.port)
    await site.start()
    log.info(f"Health-check server started on port {CFG.port}")

# --- Логика БД и Фоновые задачи ---

async def init_db():
    await subs_collection.create_index("user_id", unique=True)
    log.info("MongoDB initialized")

async def upsert_sub(user_id: int, username: str | None, full_name: str | None):
    expire = datetime.now(CFG.tz) + timedelta(days=CFG.sub_days)
    await subs_collection.update_one(
        {"user_id": user_id},
        {"$set": {"username": username or "", "full_name": full_name or "", "expire_date": expire}},
        upsert=True
    )
    return expire

async def check_expirations():
    while True:
        now = datetime.now(timezone.utc)
        cursor = subs_collection.find({"expire_date": {"$lt": now}})
        expired_users = await cursor.to_list(length=100)
        for user in expired_users:
            uid = user["user_id"]
            try:
                await bot.ban_chat_member(CFG.channel_id, uid)
                await bot.unban_chat_member(CFG.channel_id, uid)
                await subs_collection.delete_one({"user_id": uid})
                await bot.send_message(uid, "🔴 Подписка истекла.")
            except Exception as e:
                log.error(f"Error removing {uid}: {e}")
                await subs_collection.delete_one({"user_id": uid})
        await asyncio.sleep(3600)

# --- Хэндлеры ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]])
        await message.answer("Админ-панель:", reply_markup=kb)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить подписку", callback_data="check_sub")]
    ])
    await message.answer("Привет! Оплати доступ и пришли чек.", reply_markup=kb)

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"Реквизиты:\n{CFG.pay_ru}\n{CFG.pay_paypal}")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    user = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not user or user["expire_date"].replace(tzinfo=timezone.utc) < datetime.now(CFG.tz):
        await callback.message.answer("❌ Не активна.")
    else:
        await callback.message.answer(f"✅ До: {user['expire_date'].strftime('%d.%m.%Y')}")
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, 
                         caption=f"Чек от: {message.from_user.full_name}\nID: {message.from_user.id}", 
                         reply_markup=kb)
    await message.answer("⏳ Ждите подтверждения.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid_str = callback.data.split("_")
    uid = int(uid_str)
    if action == "ok":
        try:
            user_info = await bot.get_chat(uid)
            expire = await upsert_sub(uid, user_info.username, user_info.full_name)
            invite = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
            await bot.send_message(uid, f"✅ Одобрено!\n{invite.invite_link}\nДо: {expire.strftime('%d.%m.%Y')}")
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ ОДОБРЕНО")
        except Exception as e:
            log.error(f"Error in ok: {e}")
            await callback.answer("Ошибка!")
    else:
        await bot.send_message(uid, "❌ Отказано.")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n❌ ОТКАЗАНО")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    cursor = subs_collection.find({"expire_date": {"$gt": datetime.now(timezone.utc)}})
    users = await cursor.to_list(length=50)
    if not users:
        await callback.message.answer("Активных нет.")
    else:
        for u in users:
            await callback.message.answer(f"👤 {u['full_name']} | ⏳ {u['expire_date'].strftime('%d.%m.%Y')}")
    await callback.answer()

# --- Запуск ---

async def main():
    await init_db()
    # Запускаем одновременно: бота, проверку времени и микро-сервер для Render
    await asyncio.gather(
        dp.start_polling(bot),
        check_expirations(),
        run_web_server()
    )

if __name__ == "__main__":
    asyncio.run(main())
