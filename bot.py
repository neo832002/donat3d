import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import JOIN_TRANSITION
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, \
BotCommand, BotCommandScopeChat, BotCommandScopeDefault, \
ChatMemberUpdated
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
    price_ru: str = "400 руб"
    price_paypal: str = "4$"
    port: int = int(os.getenv("PORT", 10000))
    check_time: time = time(12, 0) # UTC

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Системные функции ---

async def init_db():
    await subs_collection.create_index("user_id", unique=True)

async def set_bot_commands():
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Меню / Menu"),
            BotCommand(command="stats", description="🔎 Проверить статус подписки / Check status")
        ], scope=BotCommandScopeDefault())
        
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Панель управления"),
            BotCommand(command="stats", description="📊 Статистика всех подписчиков")
        ], scope=BotCommandScopeChat(chat_id=CFG.admin_id))
    except: 
        pass

async def kick_user(user_id: int):
    try:
        user_data = await subs_collection.find_one({"user_id": user_id})
        if user_data and "invite_link" in user_data:
            try: 
                await bot.revoke_chat_invite_link(CFG.channel_id, user_data["invite_link"])
            except: 
                pass
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id)
        await subs_collection.delete_one({"user_id": user_id})
        return True
    except: 
        return False

async def check_expirations_daily():
    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), CFG.check_time)
        if now >= target: 
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            tomorrow = datetime.now() + timedelta(days=1)
            rem_cursor = subs_collection.find({
                "expire_date": {"$exists": True, "$lt": tomorrow + timedelta(hours=1), "$gt": tomorrow - timedelta(hours=1)}, 
                "status": "active", 
                "notified": {"$ne": True}
            })
            async for u in rem_cursor:
                try:
                    await bot.send_message(u["user_id"], "⚠️ **Внимание!** Подписка истекает через 24 часа.")
                    await subs_collection.update_one({"user_id": u["user_id"]}, {"$set": {"notified": True}})
                except: pass
            
            exp_cursor = subs_collection.find({"expire_date": {"$exists": True, "$lt": datetime.now()}})
            async for u in exp_cursor:
                if await kick_user(u["user_id"]):
                    try: await bot.send_message(u["user_id"], "🔴 Срок подписки вышел. Данные удалены.")
                    except: pass
        except Exception as e: 
            log.error(f"Cron Error: {e}")

async def process_check_status(user_id: int, message_to_reply: types.Message):
    u = await subs_collection.find_one({"user_id": user_id})
    if not u:
        await message_to_reply.answer("❌ Нет активной подписки.")
        return
    if "expire_date" not in u:
        await message_to_reply.answer(f"✅ Оплата подтверждена. Ваша ссылка:\n{u['invite_link']}")
        return
    if u["expire_date"] < datetime.now():
        await kick_user(user_id)
        await message_to_reply.answer("🔴 Подписка истекла.")
        return
    try:
        member = await bot.get_chat_member(CFG.channel_id, user_id)
        if member.status in ["member", "administrator", "creator"]:
            await message_to_reply.answer(f"✅ Активна до: {u['expire_date'].strftime('%d.%m.%Y %H:%M')}")
        else:
            link = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
            await subs_collection.update_one({"user_id": user_id}, {"$set": {"invite_link": link.invite_link}})
            await message_to_reply.answer(f"✅ Подписка активна до: {u['expire_date'].strftime('%d.%m.%Y')}\n\n⚠️ Вас нет в канале! Ссылка:\n{link.invite_link}")
    except:
        await message_to_reply.answer("❌ Ошибка проверки.")

# --- Обработчики ---

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=])
        await message.answer("Добро пожаловать, админ!", reply_markup=kb)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=,
    ])
    await message.answer(f"👋 Привет! Подписка на 30 дней.\n💰 Стоимость: **{CFG.price_ru}** или **{CFG.price_paypal}**.", reply_markup=kb, parse_mode="Markdown")

@dp.message(Command("stats"), F.chat.type == "private")
async def cmd_user_stats(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        await cb_stats(message)
    else:
        await process_check_status(message.from_user.id, message)

@dp.callback_query(F.data == "pay_info")
async def send_pay(callback: types.CallbackQuery):
    msg = (f"📍 **Нажмите для копирования:**\n\n🇷🇺 Карта РФ:\n`{CFG.pay_ru}`\n\n🌐 PayPal:\n`{CFG.pay_paypal}`\n\nПришлите фото чека.")
    await callback.message.answer(msg, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    await process_check_status(callback.from_user.id, callback.message)
    await callback.answer()

# --- Логика Админа ---

async def cb_stats(union: types.CallbackQuery | types.Message):
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    msg_obj = union.message if isinstance(union, types.CallbackQuery) else union
    if not users:
        await msg_obj.answer("База данных пуста.")
    else:
        for u in users:
            st = f"✅ До: {u['expire_date'].strftime('%d.%m.%Y')}" if "expire_date" in u else "⏳ Ожидает входа"
            usr = f"@{u.get('username')}" if u.get('username') else "нет username"
            kb = InlineKeyboardMarkup(inline_keyboard=}") ]])
            await msg_obj.answer(f"👤 {u.get('full_name')}\n🔗 Юзер: {usr}\n🆔 ID: `{u['user_id']}`\n📊 {st}", reply_markup=kb)
    if isinstance(union, types.CallbackQuery): await union.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    await cb_stats(callback)

@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    if callback.from_user.id == CFG.admin_id:
        uid = int(callback.data.split("_")[1])
        await kick_user(uid)
        await callback.message.edit_text("✅ Удален.")
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
        await bot.send_message(uid, f"✅ Оплата принята! Ссылка:\n{link.invite_link}")
    else:
        await bot.send_message(uid, "❌ Отказано.")
    await callback.message.delete()
    await callback.answer()

# --- Вступление и Фото ---

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_join(event: ChatMemberUpdated):
    if event.chat.id != CFG.channel_id: return
    user_id = event.from_user.id
    user_data = await subs_collection.find_one({"user_id": user_id})
    if user_data:
        if "invite_link" in user_data:
            try:
                await bot.revoke_chat_invite_link(CFG.channel_id, user_data["invite_link"])
                await subs_collection.update_one({"user_id": user_id}, {"$unset": {"invite_link": ""}})
            except: pass
        if "expire_date" not in user_data:
            expire = datetime.now() + CFG.sub_duration
            await subs_collection.update_one({"user_id": user_id}, {"$set": {"status": "active", "expire_date": expire, "notified": False}})
            try: await bot.send_message(user_id, f"✅ Доступ открыт до: {expire.strftime('%d.%m.%Y')}")
            except: pass

@dp.message(F.photo, F.chat.type == "private") # Бот реагирует на фото ТОЛЬКО в ЛС
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=])
    # Отправка админу (в ЛС админу)
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, caption=f"Новый чек!\nОт: {message.from_user.full_name}\nID: `{message.from_user.id}`", reply_markup=kb)
    # Ответ пользователю (в ЛС пользователю)
    await message.answer("⏳ Чек отправлен админу.")

async def handle_hc(request): return web.Response(text="OK")

async def main():
    await init_db()
    await set_bot_commands()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG.port).start()
    await asyncio.gather(dp.start_polling(bot), check_expirations_daily())

if __name__ == "__main__":
    asyncio.run(main())
