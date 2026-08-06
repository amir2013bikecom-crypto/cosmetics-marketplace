import os
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, select, func
from sqlalchemy.sql import expression
from datetime import datetime

from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# ========== CONFIG ==========
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SELLER_API_KEY = os.getenv("SELLER_API_KEY", "admin123")
SELLER_IDS_STR = os.getenv("SELLER_IDS", "")
SELLER_IDS = [int(x.strip()) for x in SELLER_IDS_STR.split(",") if x.strip().isdigit()]
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== DATABASE ==========
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with async_session() as session:
        yield session

# ========== MODELS ==========
class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100), nullable=False)
    price = Column(Float, nullable=False)
    old_price = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    image = Column(String(500), nullable=True)
    rating = Column(Float, default=0.0)
    reviews_count = Column(Integer, default=0)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(String(50), nullable=False, index=True)
    buyer_name = Column(String(100), nullable=True)
    buyer_phone = Column(String(50), nullable=True)
    buyer_address = Column(Text, nullable=True)
    delivery_method = Column(String(50), default="courier")
    delivery_cost = Column(Float, default=0)
    total = Column(Float, default=0)
    status = Column(String(50), default="pending")
    track_number = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    product_name = Column(String(200), nullable=False)
    quantity = Column(Integer, default=1)
    price = Column(Float, default=0)

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    buyer_id = Column(String(50), nullable=False)
    buyer_name = Column(String(100), nullable=True)
    rating = Column(Integer, default=5)
    text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# ========== PYDANTIC SCHEMAS ==========
class OrderItemCreate(BaseModel):
    product_id: int
    product_name: str
    quantity: int = 1
    price: float

class OrderCreate(BaseModel):
    buyer_id: str
    buyer_name: Optional[str] = ""
    buyer_phone: Optional[str] = ""
    buyer_address: Optional[str] = ""
    delivery_method: str = "courier"
    delivery_cost: float = 0
    total: float = 0
    items: List[OrderItemCreate]

class StatusUpdate(BaseModel):
    status: str

class TrackUpdate(BaseModel):
    track_number: str = Field(..., min_length=1)

class ReviewCreate(BaseModel):
    product_id: int
    rating: int = Field(..., ge=1, le=5)
    text: Optional[str] = ""

# ========== SEED DATA ==========
SEED_PRODUCTS = [
    {"name": "Гидрофильное масло", "category": "Уход за лицом", "price": 890, "old_price": 1200, "description": "Нежное очищение кожи. Подходит для всех типов.", "image": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=400"},
    {"name": "Сыворотка с витамином C", "category": "Уход за лицом", "price": 1290, "old_price": 1590, "description": "Осветляет и выравнивает тон кожи.", "image": "https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=400"},
    {"name": "Увлажняющий крем", "category": "Уход за лицом", "price": 750, "old_price": None, "description": "24-часовое увлажнение. Лёгкая текстура.", "image": "https://images.unsplash.com/photo-1571781926291-c477ebfd024b?w=400"},
    {"name": "Маска для лица", "category": "Уход за лицом", "price": 450, "old_price": 600, "description": "Глубокое очищение пор. 50 мл.", "image": "https://images.unsplash.com/photo-1596755389378-c31d21fd1273?w=400"},
    {"name": "Тоник для лица", "category": "Уход за лицом", "price": 590, "old_price": None, "description": "Восстанавливает pH баланс кожи.", "image": "https://images.unsplash.com/photo-1601049541289-9b1b7bbbfe19?w=400"},
    {"name": "Пилинг-гель", "category": "Уход за лицом", "price": 680, "old_price": 850, "description": "Мягкое отшелушивание. 100 мл.", "image": "https://images.unsplash.com/photo-1617897903246-719242758050?w=400"},
    {"name": "Патчи под глаза", "category": "Уход за лицом", "price": 390, "old_price": None, "description": "Убирают отёки и тёмные круги. 60 шт.", "image": "https://images.unsplash.com/photo-1616394584738-fc6e612e71b9?w=400"},
    {"name": "Бальзам для губ", "category": "Уход за лицом", "price": 290, "old_price": None, "description": "Питает и защищает губы.", "image": "https://images.unsplash.com/photo-1599305090598-fe179d501227?w=400"},
    {"name": "Мицеллярная вода", "category": "Уход за лицом", "price": 520, "old_price": 650, "description": "Очищает и снимает макияж. 200 мл.", "image": "https://images.unsplash.com/photo-1556228578-0d85b1a4d571?w=400"},
    {"name": "Солнцезащитный крем SPF 50", "category": "Уход за лицом", "price": 890, "old_price": None, "description": "Защита от UVA/UVB. 50 мл.", "image": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=400"},
    {"name": "Шампунь для объёма", "category": "Уход за волосами", "price": 640, "old_price": 790, "description": "Придаёт объём тонким волосам. 300 мл.", "image": "https://images.unsplash.com/photo-1527799820374-dcf8d9d4a388?w=400"},
    {"name": "Кондиционер для волос", "category": "Уход за волосами", "price": 590, "old_price": None, "description": "Распутывает и питает. 250 мл.", "image": "https://images.unsplash.com/photo-1620916297397-a4a5402a3c6c?w=400"},
    {"name": "Маска для волос", "category": "Уход за волосами", "price": 720, "old_price": 900, "description": "Восстановление повреждённых волос. 200 мл.", "image": "https://images.unsplash.com/photo-1522337360788-8b13dee7a37e?w=400"},
    {"name": "Масло для волос", "category": "Уход за волосами", "price": 850, "old_price": None, "description": "Блеск и защита кончиков. 50 мл.", "image": "https://images.unsplash.com/photo-1608248597279-f99d160bfbc8?w=400"},
    {"name": "Сухой шампунь", "category": "Уход за волосами", "price": 480, "old_price": None, "description": "Освежает волосы без воды. 150 мл.", "image": "https://images.unsplash.com/photo-1585751119415-3c8e2a0b7d9f?w=400"},
    {"name": "Гель для душа", "category": "Уход за телом", "price": 420, "old_price": 550, "description": "Нежное очищение кожи. 400 мл.", "image": "https://images.unsplash.com/photo-1556228578-0d85b1a4d571?w=400"},
    {"name": "Скраб для тела", "category": "Уход за телом", "price": 560, "old_price": None, "description": "Кофейный скраб. 200 г.", "image": "https://images.unsplash.com/photo-1608248543803-ba4f8c70ae0b?w=400"},
    {"name": "Лосьон для тела", "category": "Уход за телом", "price": 490, "old_price": None, "description": "Увлажняет и питает кожу. 250 мл.", "image": "https://images.unsplash.com/photo-1571781926291-c477ebfd024b?w=400"},
    {"name": "Дезодорант", "category": "Уход за телом", "price": 350, "old_price": 450, "description": "Защита на 48 часов. 50 мл.", "image": "https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=400"},
    {"name": "Крем для рук", "category": "Уход за телом", "price": 320, "old_price": None, "description": "Питает и смягчает кожу рук. 75 мл.", "image": "https://images.unsplash.com/photo-1599305090598-fe179d501227?w=400"},
    {"name": "Тушь для ресниц", "category": "Макияж", "price": 690, "old_price": 850, "description": "Объём и длина. Чёрная.", "image": "https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?w=400"},
    {"name": "Помада", "category": "Макияж", "price": 590, "old_price": None, "description": "Матовая, стойкая. Красный оттенок.", "image": "https://images.unsplash.com/photo-1586495777744-4413f21062fa?w=400"},
    {"name": "Тональный крем", "category": "Макияж", "price": 990, "old_price": 1200, "description": "Лёгкое покрытие. SPF 15. 30 мл.", "image": "https://images.unsplash.com/photo-1616683693504-3ea7e9ad6fec?w=400"},
    {"name": "Палетка теней", "category": "Макияж", "price": 1290, "old_price": None, "description": "12 оттенков. Матовые и шиммер.", "image": "https://images.unsplash.com/photo-1596462502278-27bfdd403348?w=400"},
    {"name": "Подводка для глаз", "category": "Макияж", "price": 450, "old_price": 550, "description": "Водостойкая, чёрная.", "image": "https://images.unsplash.com/photo-1631214524115-6f8eb1e4b71c?w=400"},
]

# ========== TELEGRAM BOT ==========
bot_app = None

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = {
        "inline_keyboard": [[{"text": "🛍 Открыть магазин", "web_app": {"url": WEBAPP_URL}}]]
    } if WEBAPP_URL else None
    await update.message.reply_text(
        "Добро пожаловать в Beauty Market! 🌸\n\nНажмите кнопку ниже, чтобы открыть магазин.",
        reply_markup=keyboard
    )

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ваши заказы доступны в приложении. Нажмите кнопку «Заказы».")

async def set_commands(application):
    commands = [
        BotCommand("start", "Открыть магазин"),
        BotCommand("orders", "Мои заказы"),
    ]
    await application.bot.set_my_commands(commands)

# ========== FASTAPI LIFESPAN ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed products
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Product))
        count = result.scalar()
        if count == 0:
            for p in SEED_PRODUCTS:
                session.add(Product(**p))
            await session.commit()
            logger.info(f"Seeded {len(SEED_PRODUCTS)} products")

    # Start bot
    if BOT_TOKEN:
        bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start_cmd))
        bot_app.add_handler(CommandHandler("orders", orders_cmd))
        await set_commands(bot_app)
        await bot_app.initialize()
        await bot_app.start()
        logger.info("Bot started")

    yield

    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    await engine.dispose()

# ========== APP ==========
app = FastAPI(title="Cosmetics Marketplace", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VALID_STATUSES = {"pending", "shipped", "delivered", "cancelled", "received"}

# ========== DEPENDENCIES ==========
async def verify_seller(x_seller_key: Optional[str] = Header(None)):
    if not SELLER_API_KEY or x_seller_key != SELLER_API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid seller key")
    return x_seller_key

# ========== PUSH NOTIFICATIONS ==========
async def send_telegram_message(buyer_id: str, text: str):
    if not bot_app:
        return
    try:
        await bot_app.bot.send_message(chat_id=int(buyer_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to send message to {buyer_id}: {e}")

# ========== ENDPOINTS ==========
@app.get("/health")
async def health():
    return True

@app.post("/api/v1/seed")
async def seed_products(session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(func.count()).select_from(Product))
    if result.scalar() > 0:
        return {"detail": "Already seeded"}
    for p in SEED_PRODUCTS:
        session.add(Product(**p))
    await session.commit()
    return {"success": True, "count": len(SEED_PRODUCTS)}

@app.get("/api/v1/products")
async def list_products(session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Product))
    products = result.scalars().all()
    return products

@app.get("/api/v1/products/{product_id}")
async def get_product(product_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@app.post("/api/v1/orders")
async def create_order(order: OrderCreate, session: AsyncSession = Depends(get_db)):
    db_order = Order(
        buyer_id=order.buyer_id,
        buyer_name=order.buyer_name,
        buyer_phone=order.buyer_phone,
        buyer_address=order.buyer_address,
        delivery_method=order.delivery_method,
        delivery_cost=order.delivery_cost,
        total=order.total,
        status="pending"
    )
    session.add(db_order)
    await session.flush()

    for item in order.items:
        session.add(OrderItem(
            order_id=db_order.id,
            product_id=item.product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            price=item.price
        ))

    await session.commit()

    # Notify sellers
    for seller_id in SELLER_IDS:
        try:
            await bot_app.bot.send_message(
                chat_id=seller_id,
                text=f"🛒 <b>Новый заказ #{db_order.id}!</b>\n\nСумма: {db_order.total} ₽\nСпособ доставки: {db_order.delivery_method}"
            )
        except Exception as e:
            logger.warning(f"Failed to notify seller {seller_id}: {e}")

    return {"success": True, "order_id": db_order.id}

@app.get("/api/v1/orders")
async def get_orders(buyer_id: str, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(Order).where(Order.buyer_id == buyer_id).order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()

    response = []
    for order in orders:
        items_result = await session.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
        items = items_result.scalars().all()
        response.append({
            "id": order.id,
            "buyer_id": order.buyer_id,
            "buyer_name": order.buyer_name,
            "buyer_phone": order.buyer_phone,
            "buyer_address": order.buyer_address,
            "delivery_method": order.delivery_method,
            "delivery_cost": order.delivery_cost,
            "total": order.total,
            "status": order.status,
            "track_number": order.track_number,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "items": [
                {
                    "product_id": i.product_id,
                    "product_name": i.product_name,
                    "quantity": i.quantity,
                    "price": i.price
                } for i in items
            ]
        })
    return response

@app.patch("/api/v1/orders/{order_id}/status")
async def update_status(
    order_id: int,
    update: StatusUpdate,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_seller)
):
    if update.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    old_status = order.status
    order.status = update.status
    await session.commit()

    # Push-уведомление покупателю
    if update.status == "shipped" and old_status != "shipped":
        msg = f"🚚 <b>Заказ #{order.id} отправлен!</b>\n\n"
        if order.track_number:
            msg += f"Трек-номер: <code>{order.track_number}</code>\n"
        msg += f"Способ: {order.delivery_method}\n"
        msg += f"Сумма: {order.total} ₽"
        await send_telegram_message(order.buyer_id, msg)

    if update.status == "delivered" and old_status != "delivered":
        msg = f"📦 <b>Заказ #{order.id} доставлен!</b>\n\n"
        msg += "Откройте приложение и нажмите «Подтвердить получение»"
        await send_telegram_message(order.buyer_id, msg)

    logger.info(f"Order #{order_id} status updated to {update.status}")
    return {"success": True, "order_id": order.id, "status": order.status}

@app.patch("/api/v1/orders/{order_id}/track")
async def update_track(
    order_id: int,
    update: TrackUpdate,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_seller)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.track_number = update.track_number
    order.status = "shipped"
    await session.commit()

    # Push-уведомление
    msg = f"🚚 <b>Заказ #{order.id} отправлен!</b>\n\n"
    msg += f"Трек-номер: <code>{order.track_number}</code>\n"
    msg += f"Способ: {order.delivery_method}\n"
    msg += f"Сумма: {order.total} ₽"
    await send_telegram_message(order.buyer_id, msg)

    logger.info(f"Order #{order_id} track updated: {update.track_number}")
    return {"success": True, "order_id": order.id, "track_number": order.track_number}

@app.patch("/api/v1/orders/{order_id}/receive")
async def confirm_received(
    order_id: int,
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = "received"
    await session.commit()

    # Уведомление продавцам
    for seller_id in SELLER_IDS:
        try:
            await bot_app.bot.send_message(
                chat_id=seller_id,
                text=f"✅ <b>Заказ #{order.id} получен покупателем!</b>\n\n{order.buyer_name or 'Покупатель'} подтвердил получение."
            )
        except Exception as e:
            logger.warning(f"Failed to notify seller {seller_id}: {e}")

    logger.info(f"Order #{order_id} confirmed received by buyer")
    return {"success": True, "order_id": order.id, "status": "received"}

@app.get("/api/v1/admin/orders")
async def admin_orders(
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_seller)
):
    result = await session.execute(select(Order).order_by(Order.created_at.desc()))
    orders = result.scalars().all()

    response = []
    for order in orders:
        items_result = await session.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
        items = items_result.scalars().all()
        response.append({
            "id": order.id,
            "buyer_id": order.buyer_id,
            "buyer_name": order.buyer_name,
            "buyer_phone": order.buyer_phone,
            "buyer_address": order.buyer_address,
            "delivery_method": order.delivery_method,
            "delivery_cost": order.delivery_cost,
            "total": order.total,
            "status": order.status,
            "track_number": order.track_number,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "items": [
                {
                    "product_id": i.product_id,
                    "product_name": i.product_name,
                    "quantity": i.quantity,
                    "price": i.price
                } for i in items
            ]
        })
    return response

@app.post("/api/v1/reviews")
async def create_review(
    review: ReviewCreate,
    buyer_id: str = Header(...),
    buyer_name: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_db)
):
    db_review = Review(
        product_id=review.product_id,
        buyer_id=buyer_id,
        buyer_name=buyer_name,
        rating=review.rating,
        text=review.text
    )
    session.add(db_review)
    await session.commit()

    # Update product rating
    result = await session.execute(
        select(func.avg(Review.rating), func.count(Review.id))
        .where(Review.product_id == review.product_id)
    )
    avg_rating, count = result.one()

    await session.execute(
        select(Product).where(Product.id == review.product_id)
    )
    product_result = await session.execute(select(Product).where(Product.id == review.product_id))
    product = product_result.scalar_one_or_none()
    if product:
        product.rating = round(avg_rating, 1) if avg_rating else 0.0
        product.reviews_count = count
        await session.commit()

    return {"success": True, "review_id": db_review.id}

@app.get("/api/v1/reviews")
async def get_reviews(product_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(Review).where(Review.product_id == product_id).order_by(Review.created_at.desc())
    )
    reviews = result.scalars().all()
    return reviews

# ========== RUN ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
