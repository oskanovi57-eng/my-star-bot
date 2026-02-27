import asyncio
import logging
import sqlite3
import uuid
import requests
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================== НАСТРОЙКИ (ЗАПОЛНИ СВОИМИ ДАННЫМИ) ==================
BOT_TOKEN = "8647737296:AAED2Iv94ke5-DLBsimilVXbG2NeQxVcAXw"
CRYPTOBOT_TOKEN = "539520:AAA7DDl4kqFz0j1Y3msbFKkXA0dXgAdxF1E"
YOOKASSA_PROVIDER_TOKEN = "381764678:TEST:168866"
ADMIN_ID = 7147395276  # ваш Telegram ID

# Цена за одну звезду для каждого способа оплаты
PRICE_PER_STAR_USD = 0.03      # 1 звезда = 0.03 USD
PRICE_PER_STAR_RUB = 3          # 1 звезда = 3 рубля
PRICE_PER_STAR_XTR = 1          # 1 звезда = 1 звезда Telegram (XTR)

# Лимиты для количества звёзд
MIN_STARS = 1
MAX_STARS = 1_000_000

# ================== ИНИЦИАЛИЗАЦИЯ ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================== БАЗА ДАННЫХ ==================
DB_PATH = "orders.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (order_id TEXT PRIMARY KEY,
                  user_id INTEGER,
                  username TEXT,
                  quantity INTEGER,
                  amount_usd REAL,
                  amount_rub INTEGER,
                  amount_xtr INTEGER,
                  payment_method TEXT,
                  invoice_id TEXT,
                  status TEXT DEFAULT 'pending',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def create_order(order_id, user_id, username, quantity, amount_usd, amount_rub, amount_xtr, payment_method, invoice_id=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO orders (order_id, user_id, username, quantity, amount_usd, amount_rub, amount_xtr, payment_method, invoice_id) VALUES (?,?,?,?,?,?,?,?,?)",
              (order_id, user_id, username, quantity, amount_usd, amount_rub, amount_xtr, payment_method, invoice_id))
    conn.commit()
    conn.close()

def get_pending_orders():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT order_id, user_id, username, quantity, amount_usd, amount_rub, amount_xtr, payment_method, created_at FROM orders WHERE status='pending'")
    rows = c.fetchall()
    conn.close()
    return rows

def confirm_order(order_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE orders SET status='completed' WHERE order_id=?", (order_id,))
    conn.commit()
    conn.close()
    return True

def get_order(order_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, quantity, amount_usd, amount_rub, amount_xtr, payment_method FROM orders WHERE order_id=? AND status='pending'", (order_id,))
    row = c.fetchone()
    conn.close()
    return row

init_db()

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С CRYPTOBOT API ==================
def create_crypto_invoice(amount_usd, description, payload):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "asset": "USDT",
        "amount": str(amount_usd),
        "description": description,
        "payload": payload,
        "paid_btn_name": "callback",
        "paid_btn_url": "https://t.me/your_bot",
        "hidden_message": "✅ Спасибо за покупку!"
    }
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 200:
        result = response.json()
        if result.get("ok"):
            return result["result"]
    logger.error(f"CryptoBot error: {response.text}")
    return None

# ================== СОСТОЯНИЯ FSM ==================
class OrderStates(StatesGroup):
    waiting_quantity = State()
    waiting_username = State()
    waiting_payment_method = State()

# ================== КЛАВИАТУРЫ ==================
def main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="💫 Купить звёзды", callback_data="buy")
    builder.button(text="❓ Помощь", callback_data="help")
    builder.adjust(1)
    return builder.as_markup()

def back_keyboard(target: str = "main_menu"):
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data=target)
    return builder.as_markup()

def payment_method_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 ЮKassa (карты РФ)", callback_data="pay_yookassa")
    builder.button(text="⭐ Telegram Stars", callback_data="pay_stars")
    builder.button(text="💎 CryptoBot (USDT)", callback_data="pay_crypto")
    builder.button(text="◀️ Назад", callback_data="back_to_username")
    builder.adjust(1)
    return builder.as_markup()

def admin_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Ожидающие заказы", callback_data="admin_orders")
    builder.button(text="◀️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def orders_keyboard(orders):
    builder = InlineKeyboardBuilder()
    for order in orders:
        oid, uid, username, qty, usd, rub, xtr, method, ts = order
        builder.button(text=f"Заказ {oid[-8:]} ({qty} ⭐)", callback_data=f"order_{oid}")
    builder.button(text="◀️ Назад", callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()

def order_action_keyboard(order_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить отправку", callback_data=f"confirm_{order_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{order_id}")
    builder.button(text="◀️ Назад", callback_data="admin_orders")
    builder.adjust(1)
    return builder.as_markup()

# ================== ОБРАБОТЧИКИ ==================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "✨ Добро пожаловать в магазин Telegram Stars!\n"
        "Вы можете купить любое количество звёзд от 1 до 1 000 000.\n"
        "Нажмите кнопку ниже, чтобы начать.",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data == "help")
async def help_cb(callback: types.CallbackQuery):
    text = (
        "❓ **Помощь**\n\n"
        "1. Нажмите «💫 Купить звёзды».\n"
        "2. Введите количество звёзд (целое число от 1 до 1 000 000).\n"
        "3. Введите @username получателя.\n"
        "4. Выберите способ оплаты.\n"
        "5. Оплатите счёт.\n"
        "6. После оплаты (для CryptoBot нужно подтверждение администратора) звёзды будут отправлены.\n\n"
        "При оплате через ЮKassa или Telegram Stars заказ подтверждается автоматически."
    )
    await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="Markdown")
    await callback.answer()

# ================== ПОКУПКА ==================
@dp.callback_query(F.data == "buy")
async def buy_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"🔢 Введите количество звёзд (целое число от {MIN_STARS} до {MAX_STARS}):",
        reply_markup=back_keyboard()
    )
    await state.set_state(OrderStates.waiting_quantity)
    await callback.answer()

@dp.message(OrderStates.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введите целое число.")
        return
    quantity = int(message.text)
    if quantity < MIN_STARS or quantity > MAX_STARS:
        await message.answer(f"❌ Количество должно быть от {MIN_STARS} до {MAX_STARS}.")
        return

    # Рассчитываем цены
    usd_price = round(quantity * PRICE_PER_STAR_USD, 2)
    rub_price = quantity * PRICE_PER_STAR_RUB
    xtr_price = quantity * PRICE_PER_STAR_XTR

    await state.update_data(
        quantity=quantity,
        usd_price=usd_price,
        rub_price=rub_price,
        xtr_price=xtr_price
    )

    await message.answer(
        f"✅ Вы выбрали {quantity} звёзд.\n"
        f"💰 Стоимость:\n"
        f"• {usd_price} USD (CryptoBot)\n"
        f"• {rub_price} ₽ (ЮKassa)\n"
        f"• {xtr_price} ⭐ (Telegram Stars)\n\n"
        "📝 Введите @username получателя (точно, без ошибок):",
        reply_markup=back_keyboard()
    )
    await state.set_state(OrderStates.waiting_username)

@dp.message(OrderStates.waiting_username)
async def process_username(message: types.Message, state: FSMContext):
    username = message.text.strip().replace("@", "")
    if not username or len(username) < 3:
        await message.answer("❌ Некорректный username. Введите @username:")
        return
    await state.update_data(username=username)
    data = await state.get_data()
    await message.answer(
        f"✅ Получатель: @{username}\n"
        f"Количество: {data['quantity']} ⭐\n\n"
        "Выберите способ оплаты:",
        reply_markup=payment_method_keyboard()
    )
    await state.set_state(OrderStates.waiting_payment_method)

@dp.callback_query(F.data == "back_to_username", OrderStates.waiting_payment_method)
async def back_to_username(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 Введите @username получателя (точно, без ошибок):",
        reply_markup=back_keyboard()
    )
    await state.set_state(OrderStates.waiting_username)
    await callback.answer()

# ================== CRYPTOBOT ==================
@dp.callback_query(OrderStates.waiting_payment_method, F.data == "pay_crypto")
async def pay_crypto(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = str(uuid.uuid4())[:8]

    invoice_data = create_crypto_invoice(
        amount_usd=data['usd_price'],
        description=f"{data['quantity']} stars for @{data['username']}",
        payload=order_id
    )

    if not invoice_data:
        await callback.message.edit_text("❌ Ошибка создания счёта. Попробуйте позже.")
        await state.clear()
        return

    crypto_invoice_id = invoice_data['invoice_id']
    payment_url = invoice_data['pay_url']

    create_order(
        order_id=order_id,
        user_id=callback.from_user.id,
        username=data['username'],
        quantity=data['quantity'],
        amount_usd=data['usd_price'],
        amount_rub=data['rub_price'],
        amount_xtr=data['xtr_price'],
        payment_method='crypto',
        invoice_id=str(crypto_invoice_id)
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Перейти к оплате", url=payment_url)
    await callback.message.edit_text(
        f"💳 **Счёт в CryptoBot создан!**\n"
        f"Нажмите кнопку ниже для оплаты.\n"
        f"После оплаты администратор проверит платёж.",
        reply_markup=kb.as_markup()
    )
    await state.clear()
    await callback.answer()

# ================== ЮKASSA ==================
@dp.callback_query(OrderStates.waiting_payment_method, F.data == "pay_yookassa")
async def pay_yookassa(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = str(uuid.uuid4())[:8]
    rub_amount = data['rub_price']

    prices = [LabeledPrice(label=f"{data['quantity']} ⭐", amount=rub_amount * 100)]  # в копейках

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Покупка звёзд",
        description=f"{data['quantity']} Telegram Stars для @{data['username']}",
        payload=f"yookassa_{order_id}_{data['username']}_{data['quantity']}",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter="buy_stars",
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False
    )
    create_order(
        order_id=order_id,
        user_id=callback.from_user.id,
        username=data['username'],
        quantity=data['quantity'],
        amount_usd=data['usd_price'],
        amount_rub=rub_amount,
        amount_xtr=data['xtr_price'],
        payment_method='yookassa',
        invoice_id=''
    )
    await state.update_data(order_id=order_id)
    await callback.answer()

# ================== TELEGRAM STARS ==================
@dp.callback_query(OrderStates.waiting_payment_method, F.data == "pay_stars")
async def pay_stars(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = str(uuid.uuid4())[:8]
    xtr_amount = data['xtr_price']

    prices = [LabeledPrice(label=f"{data['quantity']} ⭐", amount=xtr_amount)]

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Покупка звёзд",
        description=f"{data['quantity']} Telegram Stars для @{data['username']}",
        payload=f"stars_{order_id}_{data['username']}_{data['quantity']}",
        provider_token="",  # для XTR пустая строка
        currency="XTR",
        prices=prices,
        start_parameter="buy_stars",
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False
    )
    create_order(
        order_id=order_id,
        user_id=callback.from_user.id,
        username=data['username'],
        quantity=data['quantity'],
        amount_usd=data['usd_price'],
        amount_rub=data['rub_price'],
        amount_xtr=xtr_amount,
        payment_method='stars',
        invoice_id=''
    )
    await state.update_data(order_id=order_id)
    await callback.answer()

# ================== ОБЩИЕ ОБРАБОТЧИКИ ПЛАТЕЖЕЙ ==================
@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message, state: FSMContext):
    payload = message.successful_payment.invoice_payload
    parts = payload.split('_')
    if len(parts) >= 4:
        method = parts[0]  # yookassa или stars
        order_id = parts[1]
        username = parts[2]
        quantity = parts[3]

        # Обновляем статус заказа в БД
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE orders SET status='completed' WHERE order_id=?", (order_id,))
        conn.commit()
        conn.close()

        await message.answer(
            f"✅ Оплата прошла успешно!\n"
            f"{quantity} звёзд будут отправлены на @{username} в ближайшее время."
        )
    await state.clear()

# ================== АДМИН-ПАНЕЛЬ ==================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer("👑 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("👑 Админ-панель", reply_markup=admin_menu())
    await callback.answer()

@dp.callback_query(F.data == "admin_orders")
async def admin_orders_cb(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    orders = get_pending_orders()
    if not orders:
        await callback.message.edit_text("📭 Нет ожидающих заказов.", reply_markup=back_keyboard("admin_panel"))
        await callback.answer()
        return
    await callback.message.edit_text("📋 Ожидающие заказы:", reply_markup=orders_keyboard(orders))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("order_"))
async def show_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    order_id = callback.data.split("_", 1)[1]
    orders = get_pending_orders()
    order = next((o for o in orders if o[0] == order_id), None)
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    oid, uid, username, qty, usd, rub, xtr, method, ts = order
    text = (
        f"🆔 Заказ: {oid}\n"
        f"👤 Покупатель: {uid} (@{username})\n"
        f"🎁 Получатель: @{username}\n"
        f"📦 Количество: {qty} ⭐\n"
        f"💰 Сумма: {usd} USD / {rub} ₽ / {xtr} ⭐\n"
        f"💳 Способ: {method}\n"
        f"🕐 Время: {ts}\n\n"
        f"*После отправки звёзд нажмите «Подтвердить»*"
    )
    await callback.message.edit_text(text, reply_markup=order_action_keyboard(oid), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_"))
async def confirm_order_cb(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    order_id = callback.data.split("_", 1)[1]
    order = get_order(order_id)
    if order:
        user_id, username, qty, usd, rub, xtr, method = order
        confirm_order(order_id)
        try:
            await bot.send_message(
                user_id,
                f"✅ Ваш заказ на {qty} звёзд подтверждён!\n"
                f"Звёзды отправлены на @{username}.\n"
                f"Спасибо за покупку!"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
        await callback.message.edit_text("✅ Заказ подтверждён.", reply_markup=back_keyboard("admin_orders"))
        await callback.answer("✅ Подтверждено")
    else:
        await callback.answer("❌ Заказ не найден или уже обработан", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_order_cb(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    order_id = callback.data.split("_", 1)[1]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM orders WHERE order_id=?", (order_id,))
    row = c.fetchone()
    if row:
        user_id = row[0]
        c.execute("UPDATE orders SET status='rejected' WHERE order_id=?", (order_id,))
        conn.commit()
        try:
            await bot.send_message(user_id, "❌ Ваш платёж отклонён. Свяжитесь с поддержкой.")
        except:
            pass
    conn.close()
    await callback.message.edit_text("❌ Заказ отклонён.", reply_markup=back_keyboard("admin_orders"))
    await callback.answer("❌ Отклонено")

# ================== ЗАПУСК ==================
async def main():
    if not BOT_TOKEN or not CRYPTOBOT_TOKEN or not YOOKASSA_PROVIDER_TOKEN:
        logger.error("Проверьте настройки токенов в коде!")
        return
    logger.info("Бот с произвольным количеством звёзд запущен.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())