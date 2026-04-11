import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault

@dataclass(frozen=True)
class Config:
    token: str = "8527322806:AAFwNdIeXi2mdbIB7duY3rWoyHXxhL7Q9Pg"
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

# --- Системные настройки ---

async def set_bot_commands():
    await bot.set_my_commands(
        [BotCommand(command="start", description="🏠 Главное меню")],
        scope=BotCommandScopeDefault()
    )
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🏠 Меню"),
            BotCommand(command="stats", description="📊 Статистика"),
            BotCommand(command="clear_db", description="🧨 Очистить базу")
        ],
        scope=BotCommandScopeChat(chat_id=CFG.admin_id)
    )

async def init_db():
    await subs_collection.create_index("user_id", unique=True)
    log.info("DB Index created")

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
                try: await bot.send_message(uid, "🔴 Подписка истекла.")
                except: pass

# --- Обработчики Админа ---

@dp.message(Command("clear_db"))
async def cmd_clear_db(message: types.Message):
    if message.from_user.id != CFG.admin_id: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧨 Да, очистить всё", callback_data="confirm_clear_db")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="close_stats")]
    ])
    await message.answer("🧨 Очистить базу данных? Это удалит ВСЕХ пользователей.", reply_markup=kb)

@dp.callback_query(F.data == "confirm_clear_db")
async def cb_confirm_clear(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    await subs_collection.delete_many({})
    await callback.message.edit_text("✅ База данных очищена.")
    await callback.answer()

async def show_statistics():
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        await bot.send_message(CFG.admin_id, "База пуста.")
        return

    await bot.send_message(CFG.admin_id, f"📊 Всего пользователей: {len(users)}")
    for u in users:
        # ЗАЩИТА: проверяем наличие ключа expire_date
        expire_date = u.get('expire_date')
        date_str = expire_date.strftime('%d.%m.%Y') if expire_date else "не указана"
        
        username = f"@{u['username']}" if u.get('username') else "нет"
        text = f"👤 {u.get('full_name')}\n🔗 {username}\n📅 До: {date_str}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"terminate_{u['user_id']}")],
            [InlineKeyboardButton(text="🗑 Закрыть", callback_data="close_stats")]
        ])
        await bot.send_message(CFG.admin_id, text, reply_markup=kb)

@dp.message(Command("stats"))
async def cmd_stats_manual(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        await show_statistics()

@dp.callback_query(F.data == "close_stats")
async def cb_close_stats(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    uid = int(callback.data.split("_")[1])
    if await kick_user(uid):
        await callback.message.edit_text("✅ Пользователь удален.")
    await callback.answer()

# --- Обработчики Пользователя ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🧨 Очистить базу", callback_data="confirm_clear_db")]
        ])
        await message.answer("🛠 Панель администратора:", reply_markup=kb)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить подписку", callback_data="check_sub")]
    ])
    await message.answer("👋 Привет! Используй кнопки ниже:", reply_markup=kb)

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"РФ: `{CFG.pay_ru}`\nPayPal: `{CFG.pay_paypal}`\nПришли фото чека.", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    u = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not u or not u.get("expire_date") or u["expire_date"] < datetime.now():
        await callback.message.answer("❌ Нет подписки.")
    else:
        date_s = u['expire_date'].strftime('%d.%m.%Y')
        await callback.message.answer(f"✅ Активна до: {date_s}")
    await callback.answer()

@dp.message(F.photo, F.chat.type == "private")
async def handle_photo(message: types.Message):
    if message.from_user.id == CFG.admin_id: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ОК", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ ОТКАЗ", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, caption=f"Чек от ID: {message.from_user.id}", reply_markup=kb)
    await message.answer("⏳ Отправлено на проверку.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid = callback.data.split("_")[0], int(callback.data.split("_")[1])
    
    if action == "ok":
        try:
            u_info = await bot.get_chat(uid)
            expire = datetime.now() + timedelta(days=CFG.sub_days)
            await subs_collection.update_one({"user_id": uid}, {"$set": {"username": u_info.username or "", "full_name": u_info.full_name or "", "expire_date": expire}}, upsert=True)
            link = await bot.create_chat_invite_link(chat_id=CFG.channel_id, member_limit=1)
            await bot.send_message(uid, f"✅ Одобрено до: {expire.strftime('%d.%m.%Y')}\n{link.invite_link}")
            await callback.message.edit_caption(caption="✅ ОДОБРЕНО")
        except Exception as e: log.error(e)
    elif action == "no":
        await bot.send_message(uid, "❌ Отказано.")
        await callback.message.edit_caption(caption="❌ ОТКЛОНЕНО")
    await callback.answer()

async def main():
    await init_db()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(check_expirations())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
