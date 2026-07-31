import os
import logging
import asyncio
import requests
from dotenv import load_dotenv
from telebot import TeleBot, types

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MINI_APP_URL = os.getenv("MINI_APP_URL")
API_URL = os.getenv("API_URL")
SELLER_API_KEY = os.getenv("SELLER_API_KEY")
SELLERS = list(map(int, os.getenv("SELLER_IDS", "").split(","))) if os.getenv("SELLER_IDS") else []

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN)


@bot.message_handler(commands=["start"])
def start(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛍 Открыть магазин", web_app=types.WebAppInfo(url=MINI_APP_URL)))
    text = "Добро пожаловать в <b>Мир Косметики</b>! Здесь вы найдете лучшие товары для ухода за собой."
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("seller_"))
def handle_seller_callback(call):
    parts = call.data.split("_")
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "Некорректные данные")
        return
    action, order_id, buyer_id = parts[1], parts[2], parts[3]

    if call.from_user.id not in SELLERS:
        bot.answer_callback_query(call.id, "Доступ запрещён")
        return

    try:
        resp = requests.patch(
            f"{API_URL}/api/v1/orders/{order_id}/status",
            headers={"X-Seller-Key": SELLER_API_KEY, "Content-Type": "application/json"},
            json={"status": action},
            timeout=10
        )
        if resp.status_code == 200:
            bot.answer_callback_query(call.id, f"Статус обновлён: {action}")
            if action == "shipped":
                notify_buyer_shipped(int(buyer_id), int(order_id))
            elif action == "cancelled":
                bot.send_message(int(buyer_id), f"❌ Заказ #{order_id} отменён продавцом.")
        else:
            logger.error(f"API error {resp.status_code}: {resp.text}")
            bot.answer_callback_query(call.id, "Ошибка обновления статуса")
    except Exception as e:
        logger.exception("Seller callback error")
        bot.answer_callback_query(call.id, "Ошибка сервера")


def notify_buyer_shipped(buyer_id: int, order_id: int):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Получил", callback_data=f"buyer_received_{order_id}_{buyer_id}"),
        types.InlineKeyboardButton("❌ Не получил", callback_data=f"buyer_notreceived_{order_id}_{buyer_id}")
    )
    text = f"📦 Ваш заказ #{order_id} отправлен! Ожидайте доставку."
    try:
        bot.send_message(buyer_id, text, reply_markup=markup)
    except Exception as e:
        logger.exception(f"Failed to notify buyer {buyer_id}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("buyer_"))
def handle_buyer_callback(call):
    parts = call.data.split("_")
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "Некорректные данные")
        return
    action, order_id, expected_buyer_id = parts[1], parts[2], parts[3]

    # 🔒 КРИТИЧЕСКАЯ ПРОВЕРКА: только сам покупатель может подтвердить получение
    if str(call.from_user.id) != expected_buyer_id:
        bot.answer_callback_query(call.id, "Это не ваш заказ!")
        logger.warning(f"User {call.from_user.id} tried to act on order {order_id} of buyer {expected_buyer_id}")
        return

    if action == "received":
        try:
            resp = requests.patch(
                f"{API_URL}/api/v1/orders/{order_id}/status",
                headers={"X-Seller-Key": SELLER_API_KEY, "Content-Type": "application/json"},
                json={"status": "delivered"},
                timeout=10
            )
            if resp.status_code == 200:
                bot.answer_callback_query(call.id, "Спасибо за подтверждение!")
                bot.send_message(call.from_user.id, f"✅ Заказ #{order_id} доставлен! Спасибо за покупку.")
                notify_sellers_delivered(int(order_id), call.from_user.id)
            else:
                bot.answer_callback_query(call.id, "Ошибка подтверждения")
        except Exception as e:
            logger.exception("Buyer received error")
            bot.answer_callback_query(call.id, "Ошибка сервера")
    elif action == "notreceived":
        bot.answer_callback_query(call.id, "Сообщение отправлено продавцу")
        bot.send_message(call.from_user.id, f"⚠️ Мы уведомили продавца о проблеме с заказом #{order_id}.")
        notify_sellers_problem(int(order_id), call.from_user.id)


def notify_sellers_delivered(order_id: int, buyer_id: int):
    text = f"✅ Заказ #{order_id} доставлен покупателю!"
    for sid in SELLERS:
        try:
            bot.send_message(sid, text)
        except Exception:
            logger.exception(f"Failed to notify seller {sid}")


def notify_sellers_problem(order_id: int, buyer_id: int):
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
            logger.exception(f"Failed to notify seller {sid}")


if __name__ == "__main__":
    logger.info("Бот запущен...")
    try:
        bot.polling(none_stop=True, interval=1, timeout=20)
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
