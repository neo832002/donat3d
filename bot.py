import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import JOIN_TRANSITION
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, \
BotCommand, BotCommandScopeChat, BotCommandScopeDefault, \
ChatMemberUpdated, Message
from aiohttp import web

@dataclass(frozen=True)
class Config:
    token: str = os.getenv("TELEGRAM_TOKEN", "8527322806:AAFwNdIeXi2mdbIB7duY3rWoyHXxhL7Q9Pg") 
    admin_id: int = 942900279
    channel_id: int = -1003581309063
    db_url: str = os.getenv("MONGODB_URI")
    sub_duration: timedelta = timedelta(days=30) 
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"
    price_ru: str = "400 руб"
    price_paypal: str = "4$"
    port: int = int(os.getenv("PORT", 10000))
    check_time: time = time(12, 0)

CFG = Config()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs
bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- ФИЛЬТР: Пропускать только личные сообщения ---
class OnlyPrivateMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if event.chat.type != "private":
                return # Игнорируем всё, что не в ЛС
        return await handler(event, data)

dp.message.middleware(OnlyPrivateMiddleware())

# --- Системные функции ---
async def init_db():
    await subs_collection.create_index("user_id", unique=True)

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

async def check_expirations_daily():
    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), CFG.check_time)
        if now >= target: target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            tomorrow = datetime.now() + timedelta(days=1)
            cursor = subs_collection.find({"expire_date": {"$exists": True, "$lt": tomorrow + timedelta(hours=1), "$gt": tomorrow - timedelta(hours=1)}, "status": "active", "notified": {"$ne": True}})
            async for u in cursor:
                try:
                    await bot.send_message(u["user_id"], "⚠️ **Внимание!** Подписка истекает через 24 часа.\n⚠️ **Attention!** Subscription expires in 24 hours.")
                    await subs_collection.update_one({"user_id": u["user_id"]}, {"$set": {"notified": True}})
                except: pass
            exp_cursor = subs_collection.find({"expire_date": {"$exists": True, "$lt": datetime.now()}})
            async for u in exp_cursor:
                if await kick_user(u["user_id"]):
                    try: await bot.send_message(u["user_id"], "🔴 Срок подписки вышел. Данные удалены.\n🔴 Subscription expired. Data deleted.")
                    except: pass
        except Exception as e: log.error(f"Cron Error: {e}")

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=])
        await message.answer("Добро пожаловать, админ!", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=,
        ])
        await message.answer(f"👋 Привет! Подписка на 30 дней.\n💰 Стоимость: **{CFG.price_ru}** или **{CFG.price_paypal}**.\n\n👋 Hello! Subscription for 30 days.\n💰 Price: **{CFG.price_ru}** or **{CFG.price_paypal}**.", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, caption=f"Новый чек!\nОт: {message.from_user.full_name}\nID: `{message.from_user.id}`", reply_markup=kb)
    await message.answer("⏳ Чек отправлен админу.\n⏳ Receipt sent to admin.")

@dp.callback_query(F.data == "pay_info")
async def send_pay(callback: types.CallbackQuery):
    msg = (f"📍 **Нажмите для копирования / Tap to copy:**\n\n🇷🇺 Карта РФ (**{CFG.price_ru}**):\n`{CFG.pay_ru}`\n\n🌐 PayPal (**{CFG.price_paypal}**):\n`{CFG.pay_paypal}`\n\nПришлите фото чека после оплаты.\nSend receipt photo after payment.")
    await callback.message.answer(msg, parse_mode="Markdown"); await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    u = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not u:
        await callback.message.answer("❌ Нет активной подписки.\n❌ No active subscription.")
    elif "expire_date" not in u:
        await callback.message.answer(f"✅ Оплата подтверждена. Ваша ссылка:\n{u['invite_link']}\n\n✅ Payment confirmed. Your link above.")
    else:
        await callback.message.answer(f"✅ Активна до / Active until: {u['expire_date'].strftime('%d.%m.%Y')}")
    await callback.answer()

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    parts = callback.data.split("_")
    action, uid = parts[0], int(parts[1])
    
    if action == "ok":
        u_info = await bot.get_chat(uid)
        link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        await subs_collection.update_one({"user_id": uid}, {"$set": {"username": u_info.username or "", "full_name": u_info.full_name or "User", "status": "pending", "invite_link": link.invite_link}}, upsert=True)
        await bot.send_message(uid, f"✅ Оплата принята! Срок 30 дней пойдет после входа:\n{link.invite_link}\n\n✅ Payment accepted! Your link above.")
    else:
        await bot.send_message(uid, "❌ Отказано / Declined.")
    await callback.message.delete(); await callback.answer()

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_join(event: ChatMemberUpdated):
    if event.chat.id != CFG.channel_id: return
    user_data = await subs_collection.find_one({"user_id": event.from_user.id})
    if user_data and "expire_date" not in user_data:
        expire = datetime.now() + CFG.sub_duration
        await subs_collection.update_one({"user_id": event.from_user.id}, {"$set": {"status": "active", "expire_date": expire, "notified": False}})
        try: await bot.send_message(event.from_user.id, f"✅ Доступ открыт до: {expire.strftime('%d.%m.%Y')}\n✅ Access granted until date above.")
        except: pass

async def handle_hc(request): return web.Response(text="OK")

async def main():
    await init_db()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG.port).start()
    # Разрешаем только ЛС, колбэки и вступления (КАНАЛЫ ОТКЛЮЧЕНЫ)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member"])

if __name__ == "__main__":
    asyncio.run(main())
