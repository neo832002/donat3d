import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

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

CFG = Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sub-bot")

client = AsyncIOMotorClient(CFG.db_url)
db = client.get_database()
subs_collection = db.subs

bot = Bot(token=CFG.token)
dp = Dispatcher()

# --- Работа с БД ---

async def init_db():
    await subs_collection.create_index("user_id", unique=True)
    log.info("MongoDB Ready")

async def upsert_sub(user_id: int, username: str | None, full_name: str | None):
    expire = datetime.now(CFG.tz) + timedelta(days=CFG.sub_days)
    await subs_collection.update_one(
        {"user_id": user_id},
        {"$set": {"username": username or "", "full_name": full_name or "", "expire_date": expire}},
        upsert=True
    )
    return expire

# --- Проверка истечения (Фон) ---

async def check_expirations():
    while True:
        log.info("Checking expirations...")
        now = datetime.now(timezone.utc)
        cursor = subs_collection.find({"expire_date": {"$lt": now}})
        expired_users = await cursor.to_list(length=100)

        for user in expired_users:
            uid = user["user_id"]
            try:
                # Изгнание: бан и моментальный разбан удаляют из канала, но позволяют войти снова
                await bot.ban_chat_member(CFG.channel_id, uid)
                await bot.unban_chat_member(CFG.channel_id, uid)
                
                await subs_collection.delete_one({"user_id": uid})
                await bot.send_message(uid, "🔴 Срок подписки истек. Для возврата оплатите доступ снова.")
                log.info(f"User {uid} expelled (can rejoin later)")
            except Exception as e:
                log.error(f"Expel error for {uid}: {e}")
                await subs_collection.delete_one({"user_id": uid})

        await asyncio.sleep(3600)

# --- Хэндлеры ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == CFG.admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]])
        await message.answer("Админ-панель:", reply_markup=kb)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Реквизиты", callback_data="pay")],
        [InlineKeyboardButton(text="🔎 Моя подписка", callback_data="check_sub")]
    ])
    await message.answer("Привет! Оплати доступ в приватный канал и пришли скриншот чека.", reply_markup=kb)

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"🇷🇺 РФ: <code>{CFG.pay_ru}</code>\n🌎 PayPal: <code>{CFG.pay_paypal}</code>\n\nПришли фото чека сюда.", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    user = await subs_collection.find_one({"user_id": callback.from_user.id})
    if not user or user["expire_date"].replace(tzinfo=timezone.utc) < datetime.now(CFG.tz):
        await callback.message.answer("❌ Подписка не активна.")
    else:
        await callback.message.answer(f"✅ Активна до: {user['expire_date'].strftime('%d.%m.%Y %H:%M')}")
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
    await message.answer("⏳ Чек принят. Ожидайте подтверждения.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    
    action, uid_str = callback.data.split("_")
    uid = int(uid_str)

    if action == "ok":
        user_info = await bot.get_chat(uid)
        expire = await upsert_sub(uid, user_info.username, user_info.full_name)
        try:
            # Создаем новую ссылку (одноразовую)
            invite = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
            await bot.send_message(uid, f"✅ Оплата подтверждена!\n\nВаша ссылка для вступления:\n{invite.invite_link}\n\nСрок: до {expire.strftime('%d.%m.%Y')}")
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ ОДОБРЕНО")
        except Exception:
            await callback.answer("Ошибка при создании ссылки!")
    else:
        await bot.send_message(uid, "❌ Оплата не подтверждена администратором.")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n❌ ОТКАЗАНО")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    now = datetime.now(timezone.utc)
    cursor = subs_collection.find({"expire_date": {"$gt": now}})
    users = await cursor.to_list(length=50)
    
    if not users:
        await callback.message.answer("Активных подписок нет.")
    else:
        for u in users:
            await callback.message.answer(f"👤 {u['full_name']}\n🆔 <code>{u['user_id']}</code>\n⏳ {u['expire_date'].strftime('%d.%m.%Y')}", parse_mode="HTML")
    await callback.answer()

async def main():
    await init_db()
    await asyncio.gather(dp.start_polling(bot), check_expirations())

if __name__ == "__main__":
    asyncio.run(main())
