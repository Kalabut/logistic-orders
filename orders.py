import sqlite3
import re
import os
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

# --- КОНФІГ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [893509708]  # список ID адмінів

NAME, PHONE, FROM, TO, DATE, WEIGHT = range(6)

# --- ІНІЦІАЛІЗАЦІЯ БД ---
def init_db():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            from_address TEXT,
            to_address TEXT,
            date TEXT,
            weight TEXT,
            status TEXT DEFAULT 'не виконано',
            user_id INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# --- ПОЧАТОК ЗАМОВЛЕННЯ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Добрий день! Введіть ваше ім’я:", reply_markup=back_button())
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_back(update):
        return await cancel_dialog(update, context)
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Введіть номер телефону:", reply_markup=back_button())
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_back(update):
        return NAME
    phone = update.message.text
    if not re.match(r"^\+?[\d\s\-]{10,15}$", phone):
        await update.message.reply_text("❗ Введіть коректний номер телефону:", reply_markup=back_button())
        return PHONE
    context.user_data["phone"] = phone
    await update.message.reply_text("Звідки забрати посилку?", reply_markup=back_button())
    return FROM

async def get_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_back(update):
        return PHONE
    context.user_data["from"] = update.message.text
    await update.message.reply_text("Куди доставити посилку?", reply_markup=back_button())
    return TO

async def get_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_back(update):
        return FROM
    context.user_data["to"] = update.message.text
    await update.message.reply_text("Оберіть дату доставки (ДД.ММ.РРРР):", reply_markup=back_button())
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_back(update):
        return TO
    date_input = update.message.text
    try:
        datetime.strptime(date_input, "%d.%m.%Y")
        context.user_data["date"] = date_input
    except ValueError:
        await update.message.reply_text("❗ Невірний формат. Введіть дату у форматі ДД.ММ.РРРР:", reply_markup=back_button())
        return DATE
    await update.message.reply_text("Введіть масу посилки в кг:", reply_markup=back_button())
    return WEIGHT

async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_back(update):
        return DATE
    try:
        weight = float(update.message.text)
        if weight <= 0:
            raise ValueError
        context.user_data["weight"] = str(weight)
    except ValueError:
        await update.message.reply_text("❗ Введіть коректну масу (число > 0):", reply_markup=back_button())
        return WEIGHT

    # Збереження
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (name, phone, from_address, to_address, date, weight, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        context.user_data["name"], context.user_data["phone"],
        context.user_data["from"], context.user_data["to"],
        context.user_data["date"], context.user_data["weight"],
        update.effective_user.id
    ))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ Дякуємо! Ваше замовлення прийнято.")

    # Адмінам
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Виконано", callback_data=f"done:{order_id}"),
        InlineKeyboardButton("❌ Скасувати", callback_data=f"cancel:{order_id}")
    ]])
    text = (
        f"📦 *Нове замовлення №{order_id}*\n"
        f"👤 Ім’я: {context.user_data['name']}\n"
        f"📞 Тел: {context.user_data['phone']}\n"
        f"🚚 {context.user_data['from']} → {context.user_data['to']}\n"
        f"🗓️ Дата: {context.user_data['date']}\n"
        f"⚖️ Маса: {context.user_data['weight']} кг\n"
        f"Статус: не виконано"
    )
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown", reply_markup=keyboard)

    return ConversationHandler.END

# --- ДОДАТКОВІ ХЕЛПЕРИ ---
def back_button():
    return ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True, one_time_keyboard=True)

def is_back(update: Update):
    return update.message.text.strip().lower() in ["назад", "🔙 назад"]

# --- ПОВІДОМЛЕННЯ КЛІЄНТУ ПРИ ЗМІНІ СТАТУСУ ---
async def notify_user(order_id: int, status: str, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        try:
            await context.bot.send_message(chat_id=row[0], text=f"ℹ️ Ваше замовлення №{order_id} {status}.")
        except:
            pass

# --- ПОШУК ЗАМОВЛЕНЬ ---
async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔ Доступ заборонено.")
    if not context.args:
        return await update.message.reply_text("❗ Введіть частину імені або телефону: /find 093")
    keyword = ' '.join(context.args)
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, phone, date, status FROM orders WHERE name LIKE ? OR phone LIKE ?", (f"%{keyword}%", f"%{keyword}%"))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return await update.message.reply_text("🔍 Нічого не знайдено.")
    text = "🔍 Результати пошуку:\n\n"
    for row in rows:
        text += f"#{row[0]} | {row[1]} | {row[2]} | {row[3]} | Статус: {row[4]}\n"
    await update.message.reply_text(text)

# --- СТАТИСТИКА ---
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔ Доступ заборонено.")
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM orders")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'виконано'")
    done = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'скасовано'")
    canceled = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'не виконано'")
    pending = cursor.fetchone()[0]
    conn.close()
    await update.message.reply_text(f"📊 Всього: {total}\n✅ Виконано: {done}\n❌ Скасовано: {canceled}\n📦 Не виконано: {pending}")
### [Весь основний код залишено без змін вище — додається завершення]

# --- ОБРОБКА КНОПОК ---
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        await query.edit_message_text("⛔ У вас немає прав.")
        return

    data = query.data
    if data.startswith("done:"):
        order_id = int(data.split(":")[1])
        await update_status(order_id, "виконано", context)
        await query.edit_message_text(f"✅ Замовлення №{order_id} виконано.")
    elif data.startswith("cancel:"):
        order_id = int(data.split(":")[1])
        await update_status(order_id, "скасовано", context)
        await query.edit_message_text(f"❌ Замовлення №{order_id} скасовано.")

# --- ОНОВЛЕННЯ СТАТУСУ ---
async def update_status(order_id: int, new_status: str, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    conn.commit()
    conn.close()
    await notify_user(order_id, new_status, context)

# --- СКАСУВАННЯ ДІАЛОГУ ---
async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Процес оформлення скасовано.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- СПИСОК НЕВИКОНАНИХ ЗАМОВЛЕНЬ ---
async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔ У вас немає доступу.")

    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, phone, from_address, to_address, date, weight FROM orders WHERE status = 'не виконано'")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await update.message.reply_text("✅ Немає невиконаних замовлень.")

    text = "📋 *Невиконані замовлення:*\n\n"
    for row in rows:
        text += (
            f"🆔 ID: {row[0]}\n"
            f"👤 Ім’я: {row[1]}\n"
            f"📞 Тел: {row[2]}\n"
            f"🚚 {row[3]} → {row[4]}\n"
            f"🗓️ Дата: {row[5]}\n"
            f"⚖️ Маса: {row[6]} кг\n"
            f"{'-'*30}\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")

# --- ПОЗНАЧИТИ ЯК ВИКОНАНО ---
async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔ У вас немає прав.")
    if not context.args:
        return await update.message.reply_text("❗ Вкажіть ID: /done 0")
    try:
        order_id = int(context.args[0])
        await update_status(order_id, "виконано", context)
        await update.message.reply_text(f"✅ Замовлення №{order_id} виконано.")
    except:
        await update.message.reply_text("❌ Помилка у введенні ID.")

# --- СКАСУВАТИ ЗАМОВЛЕННЯ ---
async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔ У вас немає прав.")
    if not context.args:
        return await update.message.reply_text("❗ Вкажіть ID: /cancel_order 0")
    try:
        order_id = int(context.args[0])
        await update_status(order_id, "скасовано", context)
        await update.message.reply_text(f"❌ Замовлення №{order_id} скасовано.")
    except:
        await update.message.reply_text("❌ Помилка у введенні ID.")

# --- MAIN ---
if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_from)],
            TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_to)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weight)],
        },
        fallbacks=[CommandHandler("cancel", cancel_dialog)]
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(handle_buttons))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("orders", list_orders))
    app.add_handler(CommandHandler("done", mark_done))
    app.add_handler(CommandHandler("cancel_order", cancel_order))

    print("✅ Бот запущено...")

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(("0.0.0.0", 8000), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

app.run_polling()
