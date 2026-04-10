import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiohttp import web

@dataclass(frozen=True)
class Config:
    token: str = "8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ"
    admin_id: int = 942900279
    channel_id: int = -1003581309063
    db_url: str = os.getenv("MONGODB_URI")
    # ТЕСТ: ставим 1 минуту вместо 30 дней
    sub_duration_test: timedelta = timedelta(minutes=1)
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

async def set_bot_commands():
    await bot.set_my_commands([BotCommand(command="start", description="🏠 Start")], scope=BotCommandScopeDefault())
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Menu"),
        BotCommand(command="stats", description="📊 Stats")
    ], scope=BotCommandScopeChat(chat_id=CFG.admin_id))

async def init_db():
    await subs_collection.create_index("user_id", unique=True)

async def kick_user(user_id: int):
    """Выселение и аннуляция ссылок"""
    try:
        # Бан и немедленный разбан удаляет юзера и делает его 'left', разрешая войти позже
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id)
        await subs_collection.delete_one({"user_id": user_id})
        return True
    except Exception as e:
        log.error(f"Kick error {user_id}: {e}")
        return False

async def check_expirations_test():
    """ТЕСТ: Проверка каждые 30 секунд"""
    while True:
        now = datetime.now()
        cursor = subs_collection.find({"expire_date": {"$lt": now}})
        async for u in cursor:
            uid = u["user_id"]
            if await kick_user(uid):
                try:
                    await bot.send_message(uid, 
                        "🔴 ТЕСТ: Ваша подписка (1 мин) истекла. Вы удалены.\n"
                        "🔴 TEST: Your sub (1 min) has expired. You are removed.")
                    log.info(f"User {uid} kicked by test timer.")
                except: pass
        await asyncio.sleep(30) 

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Stats", callback_data="admin_stats")]])
        await message.answer("Admin Panel:", reply_markup=kb)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Payment", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Check sub", callback_data="check_sub")]
    ])
    await message.answer("👋 Пришлите фото чека. / Send receipt photo.", reply_markup=kb)

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        await callback.message.answer("Empty.")
    else:
        for u in users:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Kick", callback_data=f"terminate_{u['user_id']}") ]])
            await callback.message.answer(f"👤 {u.get('full_name')}\nID: {u['user_id']}\nExp: {u['expire_date']}", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    await kick_user(uid)
    await callback.message.edit_text("✅ Kicked.")
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить (на 1 мин)", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, 
                         caption=f"Чек от: {message.from_user.full_name}\nID: {message.from_user.id}", 
                         reply_markup=kb)
    await message.answer("⏳ Wait for admin.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid = callback.data.split("_")[0], int(callback.data.split("_")[1])
    
    if action == "ok":
        u_info = await bot.get_chat(uid)
        # УСТАНОВКА СРОКА 1 МИНУТА
        expire = datetime.now() + CFG.sub_duration_test
        await subs_collection.update_one({"user_id": uid}, {"$set": {"username": u_info.username, "full_name": u_info.full_name, "expire_date": expire}}, upsert=True)
        
        # ОДНОРАЗОВАЯ ССЫЛКА (member_limit=1)
        link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        
        await bot.send_message(uid, f"✅ Одобрено на 1 минуту!\nСсылка (на 1 вход): {link.invite_link}")
    else:
        await bot.send_message(uid, "❌ Declined.")
    
    await callback.message.delete()
    await callback.answer()

async def main():
    await init_db()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Фоновая проверка
    asyncio.create_task(check_expirations_test())
    
    log.info("TEST MODE STARTED (1 min sub)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
