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
        [BotCommand(command="start", description="🏠 Главное меню / Main menu")],
        scope=BotCommandScopeDefault()
    )
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🏠 Меню / Menu"),
            BotCommand(command="stats", description="📊 Статистика / Stats")
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
                try: await bot.send_message(uid, "🔴 Подписка истекла. / Subscription expired.")
                except: pass

# --- Обработчики Админа ---

async def show_statistics():
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть / Close", callback_data="close_stats")]])
        await bot.send_message(CFG.admin_id, "База пуста. / DB is empty.", reply_markup=kb)
        return

    await bot.send_message(CFG.admin_id, f"📊 Всего пользователей / Total: {len(users)}")
    for u in users:
        date_str = u['expire_date'].strftime('%d.%m.%Y')
        username = f"@{u['username']}" if u.get('username') else "none"
        text = f"👤 {u.get('full_name')}\n🔗 {username} | ID: `{u['user_id']}`\n📅 До: {date_str}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить / Terminate", callback_data=f"terminate_{u['user_id']}")],
            [InlineKeyboardButton(text="🗑 Скрыть / Hide", callback_data="close_stats")]
        ])
        await bot.send_message(CFG.admin_id, text, reply_markup=kb, parse_mode="Markdown")

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
        await callback.message.edit_text(callback.message.text + "\n\n✅ УДАЛЕН / KICKED")
    await callback.answer()

# --- Обработчики Пользователя ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика / Stats", callback_data="admin_stats")]])
        await message.answer("Панель администратора / Admin panel:", reply_markup=kb)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты / Payment", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить подписку / Check sub", callback_data="check_sub")]
    ])
    await message.answer(
        "👋 Привет! Используйте кнопки ниже для оплаты или отправьте фото чека прямо сюда.\n\n"
        "👋 Hello! Use the buttons below to pay or send the receipt photo right here.", 
        reply_markup=kb
    )

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(
        f"Реквизиты для оплаты / Payment details:\n\n💳 РФ: `{CFG.pay_ru}`\n🅿️ PayPal: `{CFG.pay_paypal}`\n\n"
        f"Пришлите фото чека после оплаты. / Send a photo of the receipt after payment.", 
        parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    u = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not u or u["expire_date"] < datetime.now():
        await callback.message.answer("❌ Нет активной подписки. / No active subscription.")
    else:
        date_s = u['expire_date'].strftime('%d.%m.%Y')
        try:
            m = await bot.get_chat_member(CFG.channel_id, callback.from_user.id)
            if m.status in ["member", "administrator", "creator"]:
                await callback.message.answer(f"✅ Активна до: {date_s}")
            else:
                link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
                await callback.message.answer(f"✅ Оплачено до {date_s}, но вы не в канале.\nВступить: {link.invite_link}")
        except: await callback.message.answer("Ошибка доступа. / Access error.")
    await callback.answer()

# Исправлено: добавлен фильтр приватного чата
@dp.message(F.photo, F.chat.type == "private")
async def handle_photo(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, 
                         caption=f"Чек от: {message.from_user.full_name}\nID: `{message.from_user.id}`", 
                         reply_markup=kb, parse_mode="Markdown")
    await message.answer("⏳ Чек отправлен на проверку. / Receipt sent.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    
    parts = callback.data.split("_")
    action = parts[0]
    uid = int(parts[1])
    
    if action == "ok":
        try:
            u_info = await bot.get_chat(uid)
            expire = datetime.now() + timedelta(days=CFG.sub_days)
            await subs_collection.update_one(
                {"user_id": uid}, 
                {"$set": {"username": u_info.username or "", "full_name": u_info.full_name or "", "expire_date": expire}}, 
                upsert=True
            )
            link = await bot.create_chat_invite_link(chat_id=CFG.channel_id, member_limit=1)
            await bot.send_message(uid, f"✅ Одобрено! До: {expire.strftime('%d.%m.%Y')}\nСсылка: {link.invite_link}")
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ ОДОБРЕНО")
        except Exception as e:
            log.error(f"Error: {e}")
    elif action == "no":
        await bot.send_message(uid, "❌ Оплата не подтверждена.")
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ ОТКЛОНЕНО")
    
    await callback.answer()

async def main():
    await init_db()
    await set_bot_commands()
    
    # Исправлено: удаление вебхука для предотвращения конфликтов сессий
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Starting bot polling...")
    
    asyncio.create_task(check_expirations())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
