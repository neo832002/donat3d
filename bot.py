import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import JOIN_TRANSITION
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ChatMemberUpdated
from aiohttp import web

@dataclass(frozen=True)
class Config:
    token: str = "8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ"
    admin_id: int = 942900279
    channel_id: int = -1003581309063
    db_url: str = os.getenv("MONGODB_URI")
    sub_duration_test: timedelta = timedelta(minutes=1) 
    check_interval: int = 20
    port: int = int(os.getenv("PORT", 10000))

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot-test")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Системные функции ---

async def init_db():
    """Инициализация индексов БД"""
    await subs_collection.create_index("user_id", unique=True)
    log.info("База данных готова.")

async def set_bot_commands():
    try:
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Start")], scope=BotCommandScopeDefault())
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Menu"),
            BotCommand(command="stats", description="📊 Stats")
        ], scope=BotCommandScopeChat(chat_id=CFG.admin_id))
    except: pass

async def kick_user(user_id: int):
    try:
        user_data = await subs_collection.find_one({"user_id": user_id})
        if user_data and "invite_link" in user_data:
            try: await bot.revoke_chat_invite_link(CFG.channel_id, user_data["invite_link"])
            except: pass
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id)
        await subs_collection.delete_one({"user_id": user_id})
        return True
    except: return False

async def check_expirations_test():
    while True:
        try:
            now = datetime.now()
            cursor = subs_collection.find({"expire_date": {"$lt": now}, "status": "active"})
            async for u in cursor:
                uid = u["user_id"]
                if await kick_user(uid):
                    try: await bot.send_message(uid, "🔴 Подписка (1 мин) истекла. / Sub expired.")
                    except: pass
        except Exception as e: log.error(f"Loop error: {e}")
        await asyncio.sleep(CFG.check_interval)

# --- Отслеживание вступления ---

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_join(event: ChatMemberUpdated):
    if event.chat.id != CFG.channel_id: return
    user_id = event.from_user.id
    user_data = await subs_collection.find_one({"user_id": user_id, "status": "pending"})
    if user_data:
        expire = datetime.now() + CFG.sub_duration_test
        await subs_collection.update_one({"user_id": user_id}, {"$set": {"status": "active", "expire_date": expire}})
        try: await bot.send_message(user_id, f"✅ Подписка активирована на 1 минуту!")
        except: pass

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Stats", callback_data="admin_stats")]])
        await message.answer("Admin Panel:", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Payment", callback_data="pay")],
            [InlineKeyboardButton(text="🔎 Check sub", callback_data="check_sub")]
        ])
        await message.answer("👋 Отправьте чек. Подписка пойдет после входа.", reply_markup=kb)

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        await callback.message.answer("База пуста.")
    else:
        for u in users:
            st = "⏳ Ждет входа" if u.get("status") == "pending" else f"✅ До: {u['expire_date'].strftime('%H:%M:%S')}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Удалить", callback_data=f"terminate_{u['user_id']}") ]])
            await callback.message.answer(f"👤 {u.get('full_name')}\n{st}", reply_markup=kb)
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, 
                         caption=f"Чек от: {message.from_user.full_name}\nID: `{message.from_user.id}`", 
                         reply_markup=kb)
    await message.answer("⏳ Чек у админа.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    data = callback.data.split("_")
    action, uid = data[0], int(data[1])
    if action == "ok":
        u_info = await bot.get_chat(uid)
        link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        await subs_collection.update_one({"user_id": uid}, {"$set": {"username": u_info.username, "full_name": u_info.full_name, "status": "pending", "invite_link": link.invite_link, "expire_date": datetime.now() + timedelta(days=365)}}, upsert=True)
        await bot.send_message(uid, f"✅ Одобрено! Вступите в канал:\n{link.invite_link}")
    await callback.message.delete()
    await callback.answer()

# --- Веб-сервер для Health Check ---
async def handle_hc(request): return web.Response(text="OK")

async def main():
    # 1. Сначала БД
    await init_db()
    
    # 2. Агрессивный сброс сессий
    for i in range(2):
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(2)
        except: pass

    await set_bot_commands()
    
    # 3. Веб-сервер
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG.port).start()

    asyncio.create_task(check_expirations_test())
    log.info("Бот запущен. Тест на 1 минуту.")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member"])

if __name__ == "__main__":
    asyncio.run(main())
