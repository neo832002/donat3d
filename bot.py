import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, time
from threading import Thread

from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession

# --- НАСТРОЙКИ (КОНФИГУРАЦИЯ) ---
TOKEN = os.getenv("TOKEN", "8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003581309063"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "942900279"))
CARD_DETAILS = os.getenv("CARD_DETAILS", "2204120115044840")
PAYPAL_DETAILS = os.getenv("PAYPAL_DETAILS", "neo832002@yahoo.com")
PRICE_RUB = os.getenv("PRICE_RUB", "300 рублей")
PRICE_USD = os.getenv("PRICE_USD", "4$")
CHECK_TIME_MSK = time(9, 0)

# --- WEB SERVER ДЛЯ RENDER (KEEP ALIVE) ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
logging.basicConfig(level=logging.INFO)
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    # Добавим поле username и country для статистики
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subs (
            user_id INTEGER PRIMARY KEY,
            expire_date DATETIME,
            username TEXT,
            country TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_subscription(user_id, days=30, username=None, country=None):
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    expire_date = datetime.now() + timedelta(days=days)
    # Вставляем или обновляем с username и country
    cur.execute("""
        INSERT OR REPLACE INTO subs (user_id, expire_date, username, country)
        VALUES (?, ?, ?, ?)
    """, (user_id, expire_date.strftime("%Y-%m-%d %H:%M:%S"), username, country))
    conn.commit()
    conn.close()

def get_sub_info(user_id):
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    cur.execute("SELECT expire_date FROM subs WHERE user_id = ?", (user_id,))
    res = cur.fetchone()
    conn.close()
    if res:
        expire_date = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        if expire_date > datetime.now():
            return expire_date
    return None

def get_all_subscribers():
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, country, expire_date FROM subs")
    rows = cur.fetchall()
    conn.close()
    return rows

class PaymentStates(StatesGroup):
    waiting = State()

# --- ОБРАБОТЧИКИ (HANDLERS) ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    expire = get_sub_info(uid)

    # Получаем username и страну из объекта message.from_user
    username = message.from_user.username or "no_username"
    country = "unknown"  # Telegram API не даёт страну напрямую, можно расширить через IP или внешние сервисы

    # Обновим данные пользователя в базе (если подписка есть или нет)
    add_subscription(uid, days=0, username=username, country=country)  # days=0 чтобы не менять дату окончания

    if expire:
        return await message.answer(f"✅ Подписка активна до: {expire.strftime('%d.%m.%Y %H:%M')}")

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="💳 Оплатить / Pay", callback_data="pay"))
    await message.answer(f"Привет! Доступ в приватный канал на 30 дней.\n💰 {PRICE_RUB} / {PRICE_USD}",
                         reply_markup=builder.as_markup())

@dp.callback_query(F.data == "pay")
async def pay_info(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(f"💰 **Реквизиты:**\nCard: `{CARD_DETAILS}`\nPayPal: `{PAYPAL_DETAILS}`\n\nПришлите скриншот чека!", parse_mode="Markdown")
    await state.set_state(PaymentStates.waiting)
    await call.answer()

@dp.message(PaymentStates.waiting, F.photo)
async def handle_screenshot(message: types.Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"conf_{message.from_user.id}"))
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id,
                         caption=f"Новая оплата от {message.from_user.id}",
                         reply_markup=kb.as_markup())
    await message.answer("Ожидайте, админ проверяет оплату...")
    await state.clear()

@dp.callback_query(F.data.startswith("conf_"))
async def approve_pay(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("❌ Только админ может подтверждать оплату.", show_alert=True)
        return
    user_id = int(call.data.split("_")[1])

    # Получим username и country из базы или обновим при подтверждении
    # Для простоты обновим подписку с текущими username и country из Telegram
    try:
        user = await bot.get_chat(user_id)
        username = user.username or "no_username"
        country = "unknown"  # Можно расширить, если есть данные
    except Exception:
        username = "unknown"
        country = "unknown"

    add_subscription(user_id, days=30, username=username, country=country)

    try:
        link = await bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
        await bot.send_message(user_id, f"✅ Оплата принята! Ваша ссылка: {link.invite_link}")
        await call.message.edit_caption(caption="✅ Подтверждено")
    except Exception as e:
        await call.message.answer(f"Ошибка при создании ссылки: {e}")
    await call.answer()

# --- Новая команда /stat для админа ---
@dp.message(Command("stat"))
async def cmd_stat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    subscribers = get_all_subscribers()
    if not subscribers:
        await message.answer("Нет подписчиков.")
        return

    lines = []
    for user_id, username, country, expire_date in subscribers:
        expire_str = expire_date if expire_date else "неизвестно"
        lines.append(f"@{username} / {user_id} / {country} / Подписка до: {expire_str}")

    # Разбиваем сообщение на части по 4000 символов (лимит Telegram)
    chunk_size = 4000
    text = "\n".join(lines)
    for i in range(0, len(text), chunk_size):
        await message.answer(text[i:i+chunk_size])

# --- ЗАПУСК ---
async def main():
    init_db()
    keep_alive()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
