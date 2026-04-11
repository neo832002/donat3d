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
    # Меню для всех пользователей
    await bot.set_my_commands(
        [BotCommand(command="start", description="🏠 Главное меню / Main menu")],
        scope=BotCommandScopeDefault()
    )
    # Меню специально для Админа (с очисткой базы)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🏠 Меню / Menu"),
            BotCommand(command="stats", description="📊 Статистика / Stats"),
            BotCommand(command="clear_db", description="🧨 Очистить базу / Clear DB")
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

@dp.message(Command("clear_db"))
async def cmd_clear_db(message: types.Message):
    if message.from_user.id != CFG.admin_id: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧨 Да, очистить всё", callback_data="confirm_clear_db")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="close_stats")]
    ])
    await message.answer(
        "🧨 **ВНИМАНИЕ!**\n\nВы собираетесь полностью очистить базу данных. "
        "Все пользователи потеряют доступ, их придется добавлять заново. "
        "Продолжить?", 
        reply_markup=kb, 
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "confirm_clear_db")
async def cb_confirm_clear(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    await subs_collection.delete_many({})
    await callback.message.edit_text("✅ База данных успешно очищена. Записей: 0.")
    await callback.answer("База очищена", show_alert=True)

async def show_statistics():
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть", callback_data="close_stats")]])
        await bot.send_message(CFG.admin_id, "База пуста.", reply_markup=kb)
        return

    await bot.send_message(CFG.admin_id, f"📊 Всего пользователей: {len(users)}")
    for u in users:
        date_raw = u.get('expire_date')
        date_str = date_raw.strftime('%d.%m.%Y') if date_raw else "не указана"
        username = f"@{u['username']}" if u.get('username') else "нет"
        
        text = f"👤 {u.get('full_name')}\n🔗 {username} | ID: `{u['user_id']}`\n📅 До: {date_str}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить подписку", callback_data=f"terminate_{u['user_id']}")],
            [InlineKeyboardButton(text="🗑 Скрыть", callback_data="close_stats")]
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
        await callback.message.edit_text(callback.message.text + "\n\n✅ ПОЛЬЗОВАТЕЛЬ УДАЛЕН")
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
        [InlineKeyboardButton(text="💳 Реквизиты для оплаты", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить мою подписку", callback_data="check_sub")]
    ])
    await message.answer(
        "👋 Добро пожаловать!\n\nИспользуйте кнопки ниже, чтобы оплатить доступ или проверить статус подписки. "
        "После оплаты отправьте фото чека прямо в этот чат.", 
        reply_markup=kb
    )

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(
        f"Реквизиты для оплаты:\n\n💳 Карта РФ: `{CFG.pay_ru}`\n🅿️ PayPal: `{CFG.pay_paypal}`\n\n"
        f"Пожалуйста, пришлите фото чека в ответ на это сообщение.", 
        parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    u = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not u or not u.get("expire_date") or u["expire_date"] < datetime.now():
        await callback.message.answer("❌ У вас нет активной подписки.")
    else:
        date_s = u['expire_date'].strftime('%d.%m.%Y')
        try:
            m = await bot.get_chat_member(CFG.channel_id, callback.from_user.id)
            if m.status in ["member", "administrator", "creator"]:
                await callback.message.answer(f"✅ Ваша подписка активна до: {date_s}")
            else:
                link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
                await callback.message.answer(f"✅ Оплачено до {date_s}, но вы еще не в канале.\nВступить: {link.invite_link}")
        except: await callback.message.answer("Ошибка доступа. Проверьте, добавлен ли бот в канал.")
    await callback.answer()

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
    await message.answer("⏳ Спасибо! Ваш чек отправлен на проверку администратору.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    
    parts = callback.data.split("_")
    action, uid = parts[0], int(parts[1])
    
    if action == "ok":
        try:
            u_info = await bot.get_chat(uid)
            expire = datetime.now() + timedelta(days=CFG.sub_days)
            await subs_collection.update_one(
                {"user_id": uid}, 
                {"$set": {
                    "username": u_info.username or "", 
                    "full_name": u_info.full_name or "", 
                    "expire_date": expire
                }}, 
                upsert=True
            )
            link = await bot.create_chat_invite_link(chat_id=CFG.channel_id, member_limit=1)
            await bot.send_message(uid, f"✅ Оплата подтверждена! Подписка активна до: {expire.strftime('%d.%m.%Y')}\n\nВаша ссылка для входа: {link.invite_link}")
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ ОДОБРЕНО")
        except Exception as e:
            log.error(f"Error: {e}")
    elif action == "no":
        await bot.send_message(uid, "❌ Извините, ваш чек не подтвержден администратором.")
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ ОТКЛОНЕНО")
    
    await callback.answer()

async def main():
    await init_db()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot started and session cleared.")
    asyncio.create_task(check_expirations())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
