import os
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MINI_APP_URL = os.getenv("MINI_APP_URL")
API_URL = os.getenv("API_URL")
SELLER_API_KEY = os.getenv("SELLER_API_KEY")
SELLERS = [7890854793, 940063562]

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=["start"])
def start(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🛍 Открыть магазин", web_app=WebAppInfo(url=MINI_APP_URL)))
    text = "Добро пожаловать в <b>Мир Косметики</b>! Здесь вы найдете лучшие товары для ухода за собой."
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("seller_"))
def handle_seller_callback(call):
    parts = call.data.split("_")
    if len(parts) < 4:
        return
    action = parts[1]
    order_id = parts[2]
    buyer_id = parts[3]
    if action == "shipped":
        try:
            resp = requests.patch(
                f"{API_URL}/api/v1/orders/{order_id}/status",
                headers={"X-Seller-Key": SELLER_API_KEY, "Content-Type": "application/json"},
                json={"status": "shipped"}
            )
            if resp.status_code == 200:
                bot.answer_callback_query(call.id, "Статус обновлен: отправлен")
                notify_buyer_shipped(buyer_id, order_id)
            else:
                bot.answer_callback_query(call.id, "Ошибка обновления статуса")
        except Exception as e:
            bot.answer_callback_query(call.id, "Ошибка сервера")
    elif action == "cancelled":
        try:
            resp = requests.patch(
                f"{API_URL}/api/v1/orders/{order_id}/status",
                headers={"X-Seller-Key": SELLER_API_KEY, "Content-Type": "application/json"},
                json={"status": "cancelled"}
            )
            if resp.status_code == 200:
                bot.answer_callback_query(call.id, "Заказ отменен")
                bot.send_message(buyer_id, f"❌ Заказ #{order_id} отменен продавцом.")
            else:
                bot.answer_callback_query(call.id, "Ошибка отмены заказа")
        except Exception as e:
            bot.answer_callback_query(call.id, "Ошибка сервера")

def notify_buyer_shipped(buyer_id, order_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Получил", callback_data=f"buyer_received_{order_id}"),
        InlineKeyboardButton("❌ Не получил", callback_data=f"buyer_notreceived_{order_id}")
    )
    text = f"📦 Ваш заказ #{order_id} отправлен! Ожидайте доставку."
    bot.send_message(buyer_id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("buyer_"))
def handle_buyer_callback(call):
    parts = call.data.split("_")
    if len(parts) < 3:
        return
    action = parts[1]
    order_id = parts[2]
    buyer_id = call.from_user.id
    if action == "received":
        try:
            resp = requests.patch(
                f"{API_URL}/api/v1/orders/{order_id}/status",
                headers={"X-Seller-Key": SELLER_API_KEY, "Content-Type": "application/json"},
                json={"status": "delivered"}
            )
            if resp.status_code == 200:
                bot.answer_callback_query(call.id, "Спасибо за подтверждение!")
                bot.send_message(buyer_id, f"✅ Заказ #{order_id} доставлен! Спасибо за покупку.")
                notify_sellers_delivered(order_id, buyer_id)
            else:
                bot.answer_callback_query(call.id, "Ошибка подтверждения")
        except Exception as e:
            bot.answer_callback_query(call.id, "Ошибка сервера")
    elif action == "notreceived":
        bot.answer_callback_query(call.id, "Сообщение отправлено продавцу")
        bot.send_message(buyer_id, f"⚠️ Мы уведомили продавца о проблеме с заказом #{order_id}.")
        notify_sellers_problem(order_id, buyer_id)

def notify_sellers_delivered(order_id, buyer_id):
    text = f"✅ Заказ #{order_id} доставлен покупателю!"
    for sid in SELLERS:
        try:
            bot.send_message(sid, text)
        except Exception:
            pass

def notify_sellers_problem(order_id, buyer_id):
    try:
        user = bot.get_chat(buyer_id)
        buyer_name = user.first_name if user else str(buyer_id)
    except Exception:
        buyer_name = str(buyer_id)
    text = f"⚠️ Проблема с заказом #{order_id}! Покупатель {buyer_name} не получил товар."
    for sid in SELLERS:
        try:
            bot.send_message(sid, text)
        except Exception:
            pass

if __name__ == "__main__":
    print("Бот запущен...")
    bot.polling(none_stop=True)
