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
    sub_days: int = 30
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"
    port: int = int(os.getenv("PORT", 10000))

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Настройка подсказок (меню команд) ---

async def set_bot_commands():
    # Подсказки для ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ (видят при вводе "/")
    user_commands = [
        BotCommand(command="start", description="🏠 Главное меню и оплата / Main menu & Payment")
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    
    # Подсказки для АДМИНА
    admin_commands = [
        BotCommand(command="start", description="🏠 Меню / Menu"),
        BotCommand(command="stats", description="📊 Статистика и управление / Stats & Management")
    ]
    await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=CFG.admin_id))
    log.info("Command hints registered")

# --- Системные функции ---

async def init_db():
    await subs_collection.create_index("user_id", unique=True)

async def kick_user(user_id: int):
    try:
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id)
        await subs_collection.delete_one({"user_id": user_id})
        return True
    except: return False

async def check_expirations():
    while True:
        now = datetime.now()
        target = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now >= target: target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        cursor = subs_collection.find({"expire_date": {"$lt": datetime.now()}})
        async for u in cursor:
            uid = u["user_id"]
            if await kick_user(uid):
                try: await bot.send_message(uid, "🔴 Подписка истекла. / Subscription expired.")
                except: pass

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика / Stats", callback_data="admin_stats")]])
        await message.answer("Панель администратора:", reply_markup=kb)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты / Payment", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить подписку / Check sub", callback_data="check_sub")]
    ])
    await message.answer(
        "👋 Привет! Используйте кнопку ниже для оплаты или отправьте фото чека прямо сюда.\n\n"
        "👋 Hello! Use the button below to pay or send the receipt photo right here.", 
        reply_markup=kb
    )

@dp.message(Command("stats"))
async def cmd_stats_manual(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        try: await message.delete()
        except: pass
        await show_statistics()

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    try: await callback.message.delete()
    except: pass
    await show_statistics()
    await callback.answer()

async def show_statistics():
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть / Close", callback_data="close_stats")]])
        await bot.send_message(CFG.admin_id, "База пуста.", reply_markup=kb)
        return

    await bot.send_message(CFG.admin_id, f"📊 Всего: {len(users)}")
    for u in users:
        date_str = u['expire_date'].strftime('%d.%m.%Y')
        text = f"👤 {u.get('full_name')}\n🆔 `{u['user_id']}`\n📅 До: {date_str}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить / Kick", callback_data=f"terminate_{u['user_id']}")],
            [InlineKeyboardButton(text="🗑 Скрыть / Hide", callback_data="close_stats")]
        ])
        await bot.send_message(CFG.admin_id, text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "close_stats")
async def cb_close_stats(callback: types.CallbackQuery):
    try: await callback.message.delete()
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    uid = int(callback.data.split("_")[1])
    if await kick_user(uid):
        await callback.message.edit_text(callback.message.text + "\n\n✅ УДАЛЕН")
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, 
                         caption=f"Чек от: {message.from_user.full_name}\nID: `{message.from_user.id}`", 
                         reply_markup=kb, parse_mode="Markdown")
    await message.answer("⏳ Чек отправлен. / Receipt sent.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid = callback.data.split("_")[0], int(callback.data.split("_")[1])
    
    if action == "ok":
        u_info = await bot.get_chat(uid)
        expire = datetime.now() + timedelta(days=CFG.sub_days)
        await subs_collection.update_one({"user_id": uid}, {"$set": {"username": u_info.username or "", "full_name": u_info.full_name or "", "expire_date": expire}}, upsert=True)
        link = await bot.create_chat_invite_link(chat_id=CFG.channel_id, member_limit=1)
        await bot.send_message(uid, f"✅ Одобрено! До: {expire.strftime('%d.%m.%Y')}\nLink: {link.invite_link}")
    else:
        await bot.send_message(uid, "❌ Отклонено. / Declined.")
    
    try: await callback.message.delete()
    except: pass
    await callback.answer()

async def main():
    await init_db()
    await set_bot_commands()
    asyncio.create_task(check_expirations())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
