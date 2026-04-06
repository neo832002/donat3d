import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import BotCommand, BotCommandScopeChat

# --- КОНФИГУРАЦИЯ / CONFIGURATION ---
TOKEN = "8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ"
CHANNEL_ID = -1003581309063 
ADMIN_ID = 942900279  
ADMIN_USERNAME = "@neo832002" 
CARD_DETAILS = "2204120115044840" 
PAYPAL_DETAILS = "neo832002@yahoo.com"
PRICE_RUB = "300 рублей"
PRICE_USD = "4$"

CHECK_TIME_MSK = time(9, 0) # 09:00 по МСК

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ / DATABASE ---
def init_db():
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS subs (user_id INTEGER PRIMARY KEY, expire_date DATETIME)")
    conn.commit()
    conn.close()

def add_subscription(user_id, days=30):
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    expire_date = datetime.now() + timedelta(days=days)
    cur.execute("INSERT OR REPLACE INTO subs (user_id, expire_date) VALUES (?, ?)", 
                (user_id, expire_date.strftime("%Y-%m-%d %H:%M:%S")))
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

def get_all_subs():
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, expire_date FROM subs")
    data = cur.fetchall()
    conn.close()
    return data

def get_expired_users():
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT user_id FROM subs WHERE expire_date <= ?", (now,))
    users = [u[0] for u in cur.fetchall()]
    conn.close()
    return users

def remove_sub(user_id):
    conn = sqlite3.connect("subscriptions.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM subs WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- ПРОВЕРКА В КАНАЛЕ ---
async def is_user_in_channel(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# --- МЕНЮ КОМАНД ---
async def set_commands(bot: Bot):
    await bot.set_my_commands([BotCommand(command="start", description="Start")], scope=types.BotCommandScopeDefault())
    admin_cmds = [
        BotCommand(command="start", description="Start"),
        BotCommand(command="stats", description="Статистика / Stats"),
        BotCommand(command="remove", description="Удалить юзера / Remove user"),
        BotCommand(command="clear_db", description="Очистить базу / Clear DB"),
        BotCommand(command="help", description="Помощь / Help")
    ]
    await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# --- ФОНОВЫЕ ЗАДАЧИ ---
async def health_check_loop():
    while True:
        try:
            count = len(get_all_subs())
            await bot.send_message(ADMIN_ID, f"🤖 Бот работает / Bot is running\n📊 Активных подписок / Active subs: {count}")
        except: pass
        await asyncio.sleep(3 * 3600)

async def check_subscriptions_loop():
    while True:
        now_msk = datetime.utcnow() + timedelta(hours=3)
        target_msk = datetime.combine(now_msk.date(), CHECK_TIME_MSK)
        if now_msk >= target_msk: target_msk += timedelta(days=1)
        await asyncio.sleep((target_msk - now_msk).total_seconds())
        for uid in get_expired_users():
            try:
                await bot.ban_chat_member(CHANNEL_ID, uid)
                await bot.unban_chat_member(CHANNEL_ID, uid)
                remove_sub(uid)
                await bot.send_message(uid, "🔴 Ваша подписка истекла / Your subscription has expired.")
            except: pass

class PaymentStates(StatesGroup):
    waiting = State()

# --- ОБРАБОТЧИКИ АДМИНА ---
@dp.message(Command("stats"), F.from_user.id == ADMIN_ID)
async def admin_stats(message: types.Message):
    subs = get_all_subs()
    if not subs: return await message.answer("Database is empty / База пуста.")
    text = "📋 **Subscribers / Подписчики:**\n"
    for uid, expire in subs:
        text += f"• ID: `{uid}` | До: {expire}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("remove"), F.from_user.id == ADMIN_ID)
async def admin_remove_user(message: types.Message):
    args = message.text.split()
    if len(args) < 2: return await message.answer("Format: /remove ID")
    uid = int(args[1])
    remove_sub(uid)
    try:
        await bot.ban_chat_member(CHANNEL_ID, uid)
        await bot.unban_chat_member(CHANNEL_ID, uid)
        await bot.send_message(uid, "🔴 Доступ аннулирован / Access revoked.")
    except: pass
    await message.answer(f"✅ User {uid} removed.")

@dp.message(Command("clear_db"), F.from_user.id == ADMIN_ID)
async def clear_database(message: types.Message):
    conn = sqlite3.connect("subscriptions.db")
    conn.cursor().execute("DELETE FROM subs")
    conn.commit()
    conn.close()
    await message.answer("⚠️ Database cleared / База очищена!")

@dp.message(Command("help"), F.from_user.id == ADMIN_ID)
async def admin_help(message: types.Message):
    await message.answer("🛠 `/stats` - list\n`/remove ID` - delete\n`/clear_db` - wipe")

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    expire = get_sub_info(uid)
    if expire:
        if await is_user_in_channel(uid):
            return await message.answer(f"✅ Активна до / Active until: {expire.strftime('%d.%m.%Y %H:%M')}")
        link = await bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
        return await message.answer(f"💎 Ссылка / Your link: {link.invite_link}")

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="💳 Оплатить / Pay", callback_data="pay"))
    builder.row(types.InlineKeyboardButton(text="⏳ Статус / Status", callback_data="stat"))
    await message.answer(f"Привет! Доступ на 30 дней / Hello! 30-day access.\n💰 {PRICE_RUB} / {PRICE_USD}", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "stat")
async def check_stat(call: types.CallbackQuery):
    expire = get_sub_info(call.from_user.id)
    msg = f"✅ До / Until: {expire.strftime('%d.%m.%Y')}" if expire else "❌ Нет подписки / No sub."
    await call.message.answer(msg)
    await call.answer()

@dp.callback_query(F.data == "pay")
async def pay_info(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(f"💰 **Реквизиты / Payment:**\nCard: `{CARD_DETAILS}`\nPayPal: `{PAYPAL_DETAILS}`\n\n📸 Пришлите чек / Send receipt!", parse_mode="Markdown")
    await state.set_state(PaymentStates.waiting)
    await call.answer()

@dp.message(PaymentStates.waiting, F.photo | F.document)
async def handle_screenshot(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="✅ Confirm", callback_data=f"conf_{uid}"))
    kb.row(types.InlineKeyboardButton(text="❌ Reject", callback_data=f"rejc_{uid}"))
    caption = f"Чек от @{message.from_user.username}\nID: {uid}"
    if message.photo: await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption, reply_markup=kb.as_markup())
    else: await bot.send_document(ADMIN_ID, message.document.file_id, caption=caption, reply_markup=kb.as_markup())
    await message.answer("✅ Отправлено на проверку / Sent for review.")
    await state.clear()

@dp.callback_query(F.data.startswith("conf_"))
async def admin_confirm(call: types.CallbackQuery):
    uid = int(call.data.split("_")[1])
    add_subscription(uid)
    link = await bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
    await bot.send_message(uid, f"🎉 Оплата принята! Ссылка / Link:\n{link.invite_link}")
    await call.message.edit_caption(caption=f"✅ Подтверждено / Confirmed ID: {uid}")

@dp.callback_query(F.data.startswith("rejc_"))
async def admin_reject(call: types.CallbackQuery):
    uid = int(call.data.split("_")[1])
    await bot.send_message(uid, f"❌ Отклонено / Rejected. Admin: {ADMIN_USERNAME}")
    await call.message.edit_caption(caption=f"🔴 Отклонено / Rejected ID: {uid}")

async def main():
    init_db()
    await set_commands(bot)
    asyncio.create_task(check_subscriptions_loop())
    asyncio.create_task(health_check_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
