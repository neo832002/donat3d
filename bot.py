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

# --- Вспомогательные функции ---

async def init_db():
    await subs_collection.create_index("user_id", unique=True)
    log.info("DB Initialized")

async def get_one_time_link():
    """Создает ссылку, которая аннулируется после 1 вступления"""
    link = await bot.create_chat_invite_link(
        chat_id=CFG.channel_id,
        member_limit=1,
        name=f"Sub_Entry_{datetime.now().strftime('%H%M%S')}"
    )
    return link.invite_link

async def kick_user(user_id: int):
    """Удаляет из канала и чистит бан-лист для возможности вернуться"""
    try:
        await bot.ban_chat_member(CFG.channel_id, user_id)
        await bot.unban_chat_member(CFG.channel_id, user_id) # Снимаем бан сразу, чтобы мог зайти потом
        await subs_collection.delete_one({"user_id": user_id})
        return True
    except Exception as e:
        log.error(f"Error kicking {user_id}: {e}")
        return False

# --- Фоновые задачи ---

async def check_expirations():
    while True:
        now = datetime.now()
        target = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        
        await asyncio.sleep((target - now).total_seconds())

        log.info("Daily Cleanup Started")
        cursor = subs_collection.find({"expire_date": {"$lt": datetime.now()}})
        async for u in cursor:
            uid = u["user_id"]
            if await kick_user(uid):
                try:
                    await bot.send_message(uid, "🔴 Ваша подписка истекла. Оплатите снова для доступа.")
                except: pass

# --- Обработчики ---

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
    await message.answer("Привет! Для доступа в приватный канал оплатите подписку и отправьте скриншот чека в этот чат.", reply_markup=kb)

@dp.callback_query(F.data == "pay")
async def send_pay(callback: types.CallbackQuery):
    await callback.message.answer(f"Реквизиты для оплаты:\n\n💳 РФ: `{CFG.pay_ru}`\n🅿️ PayPal: `{CFG.pay_paypal}`\n\nПосле оплаты пришлите фото чека.", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    cursor = subs_collection.find()
    users = await cursor.to_list(length=None)
    
    if not users:
        await callback.message.answer("База данных пуста.")
    else:
        await callback.message.answer(f"📊 Всего записей в базе: {len(users)}")
        for u in users:
            username = f"@{u['username']}" if u.get('username') else "нет"
            # Формат: Имя, Логин, ID, Дата
            date_str = u['expire_date'].strftime('%d.%m.%Y')
            text = (f"👤 **Имя:** {u.get('full_name')}\n"
                    f"🔗 **Логин:** {username}\n"
                    f"🆔 **ID:** `{u['user_id']}`\n"
                    f"📅 **До:** {date_str}")
            
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Отменить подписку", callback_data=f"terminate_{u['user_id']}")
            ]])
            await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("terminate_"))
async def terminate_sub(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    uid = int(callback.data.split("_")[1])
    
    if await kick_user(uid):
        await callback.message.edit_text(callback.message.text + "\n\n✅ **ПОДПИСКА АННУЛИРОВАНА**", parse_mode="Markdown")
        try: await bot.send_message(uid, "🔴 Ваша подписка была аннулирована администратором.")
        except: pass
    else:
        await callback.answer("Ошибка при удалении")
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")
    ]])
    await bot.send_photo(
        CFG.admin_id, 
        message.photo[-1].file_id, 
        caption=f"Чек от: {message.from_user.full_name}\nЛогин: @{message.from_user.username}\nID: `{message.from_user.id}`", 
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await message.answer("⏳ Чек отправлен на проверку администратору.")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != CFG.admin_id: return
    action, uid = callback.data.split("_")[0], int(callback.data.split("_")[1])
    
    if action == "ok":
        try:
            u_info = await bot.get_chat(uid)
            # Сохраняем в базу
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
            # Генерируем одноразовую ссылку
            link = await get_one_time_link()
            await bot.send_message(uid, f"✅ Оплата принята!\n\nВаша одноразовая ссылка для вступления:\n{link}\n\nПодписка активна до: {expire.strftime('%d.%m.%Y')}")
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ ОДОБРЕНО")
        except Exception as e:
            await callback.answer(f"Ошибка: {e}")
    else:
        try: await bot.send_message(uid, "❌ Ваш чек был отклонен. Свяжитесь с администратором, если это ошибка.")
        except: pass
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ ОТКЛОНЕНО")
    await callback.answer()

# --- Веб-сервер для Healthcheck ---
async def handle_hc(request): return web.Response(text="Bot is alive")

async def main():
    await init_db()
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG.port).start()
    
    asyncio.create_task(check_expirations())
    log.info("Bot Started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
