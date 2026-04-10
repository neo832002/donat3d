import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    log.info("System Ready")

async def upsert_sub(user_id: int, username: str | None, full_name: str | None):
    expire = datetime.now() + timedelta(days=CFG.sub_days)
    await subs_collection.update_one(
        {"user_id": user_id},
        {"$set": {"username": username or "", "full_name": full_name or "", "expire_date": expire}},
        upsert=True
    )
    return expire

async def check_expirations():
    """Проверка подписок строго в 12:00 каждый день"""
    while True:
        now = datetime.now()
        # Вычисляем время до следующих 12:00
        target = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        
        wait_seconds = (target - now).total_seconds()
        log.info(f"Следующая проверка запланирована на 12:00 (через {wait_seconds/3600:.2f} ч.)")
        await asyncio.sleep(wait_seconds)

        log.info("Запуск ежедневной очистки (12:00)...")
        try:
            cursor = subs_collection.find({"expire_date": {"$lt": datetime.now()}})
            async for u in cursor:
                uid = u["user_id"]
                try:
                    await bot.ban_chat_member(CFG.channel_id, uid)
                    await bot.unban_chat_member(CFG.channel_id, uid)
                    await subs_collection.delete_one({"user_id": uid})
                    await bot.send_message(uid, "🔴 Ваша подписка истекла и вы были удалены из канала.")
                except Exception as e:
                    log.error(f"Не удалось удалить {uid}: {e}")
                    await subs_collection.delete_one({"user_id": uid})
        except Exception as e:
            log.error(f"Ошибка в цикле проверки: {e}")

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

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"Реквизиты:\nРФ: {CFG.pay_ru}\nPayPal: {CFG.pay_paypal}")
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = await subs_collection.find_one({"user_id": user_id})
    
    if not user_data or user_data["expire_date"] < datetime.now():
        await callback.message.answer("❌ Ваша подписка не активна.")
    else:
        expire_str = user_data["expire_date"].strftime('%d.%m.%Y %H:%M')
        try:
            member = await bot.get_chat_member(CFG.channel_id, user_id)
            if member.status in ["member", "administrator", "creator", "restricted"]:
                await callback.message.answer(f"✅ Подписка активна до: {expire_str}")
            else:
                inv = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
                await callback.message.answer(f"⚠️ Подписка есть (до {expire_str}), но вы не в канале.\nВступить: {inv.invite_link}")
        except:
            await callback.message.answer("Ошибка доступа к каналу.")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    cursor = subs_collection.find({"expire_date": {"$gt": datetime.now()}})
    users = await cursor.to_list(length=None)
    if not users:
        await callback.message.answer("Активных нет.")
    else:
        await callback.message.answer(f"📊 Всего: {len(users)}")
        for u in users:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Удалить", callback_data=f"terminate_{u['user_id']}") ]])
            await callback.message.answer(f"👤 {u.get('full_name')}\nID: {u['user_id']}\nДо: {u['expire_date']}", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    uid = int(callback.data.split("_")[1])
    try:
        await bot.ban_chat_member(CFG.channel_id, uid)
        await bot.unban_chat_member(CFG.channel_id, uid)
        await subs_collection.delete_one({"user_id": uid})
        await callback.message.edit_text(f"✅ Пользователь {uid} удален.")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}")
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
    data = callback.data.split("_")
    action, uid = data[0], int(data[1])
    
    if action == "ok":
        u_info = await bot.get_chat(uid)
        expire = await upsert_sub(uid, u_info.username, u_info.full_name)
        inv = await bot.create_chat_invite_link(CFG.channel_id, member_limit=1)
        await bot.send_message(uid, f"✅ Одобрено!\nСсылка: {inv.invite_link}\nДо: {expire.strftime('%d.%m.%Y')}")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ ОДОБРЕНО")
    else:
        await bot.send_message(uid, "❌ Чек отклонен.")
        await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n❌ ОТКЛОНЕНО")
    await callback.answer()

async def main():
    await init_db()
    asyncio.create_task(check_expirations())
    asyncio.create_task(run_web_server())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
