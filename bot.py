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
bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- МИДЛВАРЬ: Блокируем всё, что не из лички ---
class PrivateOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if event.chat.type != "private":
                return
        return await handler(event, data)

dp.message.middleware(PrivateOnlyMiddleware())

async def init_db():
    client = AsyncIOMotorClient(CFG.db_url)
    db = client["sub_bot_db"]
    await db.subs.create_index("user_id", unique=True)
    return db.subs

async def kick_user(user_id: int, subs_coll):
    try:
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id)
        await subs_coll.delete_one({"user_id": user_id})
        return True
    except: return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        btn =]
        kb = InlineKeyboardMarkup(inline_keyboard=btn)
        await message.answer("Добро пожаловать, админ!", reply_markup=kb)
    else:
        btn =,
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=btn)
        await message.answer(f"👋 Привет! Подписка на 30 дней.\n💰 Стоимость: **{CFG.price_ru}** или **{CFG.price_paypal}**.\n\n👋 Hello! Subscription for 30 days.\n💰 Price: **{CFG.price_ru}** or **{CFG.price_paypal}**.", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    # ТУТ ИСПРАВЛЕННЫЕ СКОБКИ
    btn =]
    kb = InlineKeyboardMarkup(inline_keyboard=btn)
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, caption=f"Новый чек!\nОт: {message.from_user.full_name}\nID: `{message.from_user.id}`", reply_markup=kb)
    await message.answer("⏳ Чек отправлен админу.\n⏳ Receipt sent to admin.")

@dp.callback_query(F.data == "pay_info")
async def send_pay(callback: types.CallbackQuery):
    msg = (f"📍 **Нажмите для копирования / Tap to copy:**\n\n🇷🇺 Карта РФ (**{CFG.price_ru}**):\n`{CFG.pay_ru}`\n\n🌐 PayPal (**{CFG.price_paypal}**):\n`{CFG.pay_paypal}`\n\nПришлите фото чека после оплаты.\nSend receipt photo after payment.")
    await callback.message.answer(msg, parse_mode="Markdown"); await callback.answer()

async def handle_hc(request): return web.Response(text="OK")

async def main():
    subs_coll = await init_db()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG.port).start()
    # КАНАЛЫ НЕ СЛУШАЕМ (allowed_updates)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member"])

if __name__ == "__main__":
    asyncio.run(main())
