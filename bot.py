import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import JOIN_TRANSITION
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

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Системные функции ---

async def set_bot_commands():
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🏠 Меню / Menu"),
            BotCommand(command="my_sub", description="🔎 Подписка / Subscription")
        ],
        scope=BotCommandScopeDefault()
    )
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
        cursor = subs_collection.find({"expire_date": {"$lt": now}})
        async for u in cursor:
            uid = u["user_id"]
            if await kick_user(uid):
                try: 
                    await bot.send_message(uid, "🔴 Подписка истекла. / Subscription expired.")
                except: pass
        await asyncio.sleep(3600)

# --- Логика активации ПРИ ВСТУПЛЕНИИ (без заявки) ---

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_join(event: types.ChatMemberUpdated):
    if event.chat.id != CFG.channel_id: return
    
    uid = event.from_user.id
    user = await subs_collection.find_one({"user_id": uid})
    
    # Если пользователь оплатил, но время еще не пошло
    if user and user.get("status") == "paid" and not user.get("expire_date"):
        expire = datetime.now() + timedelta(days=CFG.sub_days)
        await subs_collection.update_one(
            {"user_id": uid},
            {"$set": {"expire_date": expire, "status": "active"}}
        )
        try:
            await bot.send_message(uid, 
                f"✅ Вы вступили в канал! Подписка активирована.\nДо: {expire.strftime('%d.%m.%Y')}\n\n"
                f"✅ You joined the channel! Subscription activated.\nUntil: {expire.strftime('%d.%m.%Y')}")
        except: pass

# --- Обработчики Админа ---

@dp.message(Command("clear_db"))
async def cmd_clear_db(message: types.Message):
    if message.from_user.id != CFG.admin_id: return
    kb = InlineKeyboardMarkup(inline_keyboard=])
    await message.answer("Очистить базу данных? / Clear database?", reply_markup=kb)

@dp.callback_query(F.data == "conf_clear")
async def cb_clear(callback: types.CallbackQuery):
    await subs_collection.delete_many({})
    await callback.message.edit_text("✅ База пуста. / DB empty.")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != CFG.admin_id: return
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    if not users:
        await message.answer("База пуста. / DB is empty.")
        return
    
    for u in users:
        exp = u.get("expire_date")
        date_s = exp.strftime('%d.%m.%Y') if exp else "Ожидает вступления / Waiting for join"
        text = f"👤 {u.get('full_name')}\nID: `{u['user_id']}`\n📅 До: {date_s}"
        kb = InlineKeyboardMarkup(inline_keyboard=}")]])
        await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("kick_"))
async def cb_kick(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    if await kick_user(uid):
        await callback.message.edit_text("✅ Удален. / Kicked.")

# --- Обработчики Пользователя ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=])
        await message.answer("🛠 Админ-панель / Admin panel:", reply_markup=kb)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=,
    ])
    await message.answer("👋 Привет! Оплатите доступ и пришлите чек.\n👋 Hello! Pay for access and send a receipt.", reply_markup=kb)

@dp.message(Command("my_sub"))
@dp.callback_query(F.data == "check_my_sub")
async def check_user_sub(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    user = await subs_collection.find_one({"user_id": user_id})
    msg = event if isinstance(event, types.Message) else event.message

    if not user:
        await msg.answer("❌ Нет подписки. / No subscription.")
        return

    if user.get("expire_date"):
        try:
            member = await bot.get_chat_member(CFG.channel_id, user_id)
            if member.status in ["member", "administrator", "creator"]:
                await msg.answer(f"✅ Активна до: {user['expire_date'].strftime('%d.%m.%Y')}\n✅ Active until: {user['expire_date'].strftime('%d.%m.%Y')}")
            else:
                # Генерируем новую одноразовую ссылку, если пользователь вышел
                link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
                await msg.answer(f"✅ Оплачено до {user['expire_date'].strftime('%d.%m.%Y')}, но вы вышли.\nСсылка: {link.invite_link}\n\n✅ Paid until {user['expire_date'].strftime('%d.%m.%Y')}, but you left.\nLink: {link.invite_link}")
        except:
            await msg.answer("Ошибка / Error")
    elif user.get("status") == "paid":
        link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        await msg.answer(f"⏳ Оплата подтверждена. Просто вступите в канал по ссылке (время пойдет с момента вступления):\n{link.invite_link}\n\n⏳ Payment confirmed. Just join the channel (time starts from the moment you join):\n{link.invite_link}")
    
    if isinstance(event, types.CallbackQuery): await event.answer()

@dp.callback_query(F.data == "pay")
async def cb_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"💳 РФ: `{CFG.pay_ru}`\n🅿️ PayPal: `{CFG.pay_paypal}`\n\nПришлите чек. / Send receipt.")
    await callback.answer()

@dp.message(F.photo, F.chat.type == "private")
async def handle_receipt(message: types.Message):
    if message.from_user.id == CFG.admin_id: return
    kb = InlineKeyboardMarkup(inline_keyboard=])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, caption=f"Чек от {message.from_user.full_name}\nID: `{message.from_user.id}`", reply_markup=kb)
    await message.answer("⏳ Ожидайте проверки. / Wait for verification.")

@dp.callback_query(F.data.startswith(("app_", "ref_")))
async def cb_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    parts = callback.data.split("_")
    action, uid = parts[0], int(parts[1])
    
    if action == "app":
        u_info = await bot.get_chat(uid)
        await subs_collection.update_one(
            {"user_id": uid}, 
            {"$set": {"username": u_info.username or "", "full_name": u_info.full_name or "", "status": "paid"}}, 
            upsert=True
        )
        link = await bot.create_chat_invite_link(chat_id=CFG.channel_id, member_limit=1)
        await bot.send_message(uid, f"✅ Оплата принята! Ссылка для входа:\n{link.invite_link}\n\n✅ Payment accepted! Join link:\n{link.invite_link}")
        await callback.message.edit_caption(caption="✅ ОДОБРЕНО / APPROVED")
    else:
        await bot.send_message(uid, "❌ Отказано. / Declined.")
        await callback.message.edit_caption(caption="❌ ОТКЛОНЕНО / DECLINED")
    await callback.answer()

async def main():
    await init_db()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(check_expirations())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
