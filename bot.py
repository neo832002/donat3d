import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import JOIN_TRANSITION
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, ChatMemberUpdated
from aiohttp import web

@dataclass(frozen=True)
class Config:
    token: str = os.getenv("BOT_TOKEN", "8527322806:AAFwNdIeXi2mdbIB7duY3rWoyHXxhL7Q9Pg") 
    admin_id: int = 942900279
    channel_id: int = -1003581309063
    db_url: str = os.getenv("MONGODB_URI")
    sub_duration: timedelta = timedelta(days=30) 
    pay_ru: str = "2204120115044840"
    pay_paypal: str = "neo832002@yahoo.com"
    port: int = int(os.getenv("PORT", 10000))
    check_time: time = time(12, 0) # Проверка в 12:00 (UTC)

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Системная логика ---

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
            # 1. Уведомление за 24 часа
            tomorrow = datetime.now() + timedelta(days=1)
            rem_cursor = subs_collection.find({
                "expire_date": {"$lt": tomorrow + timedelta(hours=1), "$gt": tomorrow - timedelta(hours=1)},
                "status": "active", "notified": {"$ne": True}
            })
            async for u in rem_cursor:
                try:
                    await bot.send_message(u["user_id"], "⚠️ **Внимание!** Подписка истекает через 24 часа. Продлите доступ, чтобы остаться в канале.")
                    await subs_collection.update_one({"user_id": u["user_id"]}, {"$set": {"notified": True}})
                except: pass

            # 2. Удаление истекших
            exp_cursor = subs_collection.find({"expire_date": {"$lt": datetime.now()}, "status": "active"})
            async for u in exp_cursor:
                if await kick_user(u["user_id"]):
                    try: await bot.send_message(u["user_id"], "🔴 Срок подписки вышел. Вы были исключены.")
                    except: pass
        except Exception as e: log.error(f"Cron error: {e}")

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay_info")],
        [InlineKeyboardButton(text="🔎 Статус подписки", callback_data="check_sub")]
    ])
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=kb)

@dp.callback_query(F.data == "pay_info")
async def send_pay(callback: types.CallbackQuery):
    msg = (
        "📍 **Нажмите на реквизиты для копирования:**\n\n"
        f"🇷🇺 Карта РФ:\n`{CFG.pay_ru}`\n\n"
        f"🌐 PayPal:\n`{CFG.pay_paypal}`\n\n"
        "После оплаты пришлите **фото чека** в этот чат."
    )
    await callback.message.answer(msg, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    u = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not u or u.get("status") == "pending":
        await callback.message.answer("❌ У вас нет активной подписки.")
    else:
        await callback.message.answer(f"✅ Подписка активна до: {u['expire_date'].strftime('%d.%m.%Y %H:%M')}")
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
    await message.answer("⏳ Чек принят. Админ проверит его в ближайшее время.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid = callback.data.split("_")[0], int(callback.data.split("_")[1])
    
    if action == "ok":
        link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        await subs_collection.update_one(
            {"user_id": uid}, 
            {"$set": {"status": "pending", "invite_link": link.invite_link, "notified": False}}, upsert=True
        )
        await bot.send_message(uid, f"✅ Оплата принята! Ссылка для входа:\n{link.invite_link}")
    else:
        await bot.send_message(uid, "❌ Оплата не подтверждена. Проверьте данные и попробуйте снова.")
    await callback.message.delete()
    await callback.answer()

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_join(event: ChatMemberUpdated):
    if event.chat.id != CFG.channel_id: return
    user_id = event.from_user.id
    user_data = await subs_collection.find_one({"user_id": user_id, "status": "pending"})
    if user_data:
        expire = datetime.now() + CFG.sub_duration
        await subs_collection.update_one({"user_id": user_id}, {"$set": {"status": "active", "expire_date": expire}})
        try: await bot.send_message(user_id, f"✅ Доступ активирован на 30 дней! До {expire.strftime('%d.%m.%Y')}")
        except: pass

# --- Запуск ---

async def handle_hc(request): return web.Response(text="Bot is Live")

async def main():
    await init_db()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG.port).start()
    await asyncio.gather(dp.start_polling(bot), check_expirations_daily())

if __name__ == "__main__":
    asyncio.run(main())
