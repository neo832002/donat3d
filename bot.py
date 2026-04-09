import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    tz: timezone = timezone.utc
    port: int = int(os.getenv("PORT", 10000))

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client["sub_bot_db"] 
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Системные функции ---

async def handle_render_healthcheck(request):
    return web.Response(text="Bot is alive")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_render_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", CFG.port)
    await site.start()

async def init_db():
    await subs_collection.create_index("user_id", unique=True)
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.delete_my_commands()
    log.info("System Ready")

async def upsert_sub(user_id: int, username: str | None, full_name: str | None):
    expire = datetime.now(CFG.tz) + timedelta(days=CFG.sub_days)
    await subs_collection.update_one(
        {"user_id": user_id},
        {"$set": {"username": username or "", "full_name": full_name or "", "expire_date": expire}},
        upsert=True
    )
    return expire

async def check_expirations():
    while True:
        now = datetime.now(timezone.utc)
        cursor = subs_collection.find({"expire_date": {"$lt": now}})
        expired = await cursor.to_list(length=100)
        for u in expired:
            uid = u["user_id"]
            try:
                await bot.ban_chat_member(CFG.channel_id, uid)
                await bot.unban_chat_member(CFG.channel_id, uid)
                await subs_collection.delete_one({"user_id": uid})
                await bot.send_message(uid, "🔴 Подписка истекла.")
            except:
                await subs_collection.delete_one({"user_id": uid})
        await asyncio.sleep(3600)

# --- Обработка команд ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]])
        await message.answer("Админ-панель:", reply_markup=kb)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Проверить подписку", callback_data="check_sub")]
    ])
    await message.answer("Привет! Оплати доступ и пришли чек.", reply_markup=kb)

# Команда Статистика для админа
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        await show_stats(message)

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    await show_stats(callback.message)
    await callback.answer()

async def show_stats(message: types.Message):
    now = datetime.now(timezone.utc)
    cursor = subs_collection.find({"expire_date": {"$gt": now}})
    users = await cursor.to_list(length=100)
    
    if not users:
        await message.answer("Активных подписчиков нет.")
        return

    await message.answer(f"📊 <b>Активных пользователей: {len(users)}</b>", parse_mode="HTML")
    for u in users:
        # ИСПРАВЛЕНО: Правильная кнопка удаления
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Удалить из канала", callback_data=f"terminate_{u['user_id']}")
        ]])
        text = f"👤 {u['full_name']}\n🆔 <code>{u['user_id']}</code>\n⏳ До: {u['expire_date'].strftime('%d.%m.%Y %H:%M')}"
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

# ИСПРАВЛЕНО: Хэндлер удаления пользователя
@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    
    user_id = int(callback.data.split("_")[1])
    try:
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id)
        await subs_collection.delete_one({"user_id": user_id})
        
        await callback.message.edit_text(f"🗑 Пользователь {user_id} удален.")
        await bot.send_message(user_id, "🔴 Ваша подписка аннулирована администратором.")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}")
    await callback.answer()

# --- Остальные хэндлеры ---

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"Реквизиты:\nРФ: {CFG.pay_ru}\nPayPal: {CFG.pay_paypal}")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    user = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not user or user["expire_date"].replace(tzinfo=timezone.utc) < datetime.now(CFG.tz):
        await callback.message.answer("❌ Подписка не активна.")
    else:
        await callback.message.answer(f"✅ Активна до: {user['expire_date'].strftime('%d.%m.%Y')}")
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(CFG.admin_id, message.photo[-1].file_id, 
                         caption=f"Чек от: {message.from_user.full_name}\nID: {message.from_user.id}", 
                         reply_markup=kb)
    await message.answer("⏳ Чек отправлен админу.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid = callback.data.split("_")
    uid = int(uid)
    if action == "ok":
        u_info = await bot.get_chat(uid)
        expire = await upsert_sub(uid, u_info.username, u_info.full_name)
        inv = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        await bot.send_message(uid, f"✅ Одобрено!\n{inv.invite_link}\nДо: {expire.strftime('%d.%m.%Y')}")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ ОДОБРЕНО")
    else:
        await bot.send_message(uid, "❌ Отказано.")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n❌ ОТКАЗАНО")
    await callback.answer()

async def main():
    await init_db()
    await asyncio.gather(dp.start_polling(bot), check_expirations(), run_web_server())

if __name__ == "__main__":
    asyncio.run(main())
