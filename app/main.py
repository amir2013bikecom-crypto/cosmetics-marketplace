import os
import logging
from typing import Optional, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship, selectinload
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, select, desc
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

# ─── CONFIG ─────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
SELLER_IDS = [int(x.strip()) for x in os.getenv("SELLER_IDS", "").split(",") if x.strip()]
SELLER_API_KEY = os.getenv("SELLER_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ─── DATABASE ───────────────────────────────────────────────────────
Base = declarative_base()

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    old_price = Column(Float, nullable=True)
    image = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    rating = Column(Float, default=0.0)
    reviews_count = Column(Integer, default=0)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, nullable=False)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    price = Column(Float, nullable=False)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(String, nullable=False, index=True)
    buyer_name = Column(String, nullable=True)
    buyer_phone = Column(String, nullable=True)
    buyer_address = Column(Text, nullable=True)
    status = Column(String, default="pending")
    total = Column(Float, default=0.0)
    delivery_method = Column(String, default="courier")
    delivery_cost = Column(Float, default=0.0)
    track_number = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    items = relationship("OrderItem", lazy="selectin")

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    buyer_id = Column(String, nullable=False)
    buyer_name = Column(String, nullable=True)
    rating = Column(Integer, default=5)
    text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ─── PYDANTIC SCHEMAS ───────────────────────────────────────────────
class OrderItemIn(BaseModel):
    product_id: int
    product_name: str
    quantity: int = Field(..., ge=1)
    price: float = Field(..., ge=0)

class OrderCreate(BaseModel):
    buyer_id: str
    buyer_name: Optional[str] = ""
    buyer_phone: Optional[str] = ""
    buyer_address: Optional[str] = ""
    items: List[OrderItemIn]
    total: float = Field(..., ge=0)
    delivery_method: Optional[str] = "courier"
    delivery_cost: Optional[float] = 0.0

    @validator("buyer_phone")
    def validate_phone(cls, v):
        if v and not v.replace("+", "").replace("-", "").replace(" ", "").isdigit():
            raise ValueError("Некорректный номер телефона")
        return v

VALID_STATUSES = {"pending", "shipped", "delivered", "cancelled"}

class StatusUpdate(BaseModel):
    status: str

    @validator("status")
    def validate_status(cls, v):
        if v not in VALID_STATUSES:
            raise ValueError(f"Статус должен быть одним из: {VALID_STATUSES}")
        return v

class TrackUpdate(BaseModel):
    track_number: str = Field(..., min_length=3)

class ReviewCreate(BaseModel):
    product_id: int
    buyer_id: str
    buyer_name: Optional[str] = ""
    rating: int = Field(5, ge=1, le=5)
    text: Optional[str] = Field("", max_length=2000)

# ─── BOT ────────────────────────────────────────────────────────────
bot_app: Optional[Application] = None

async def start_bot():
    global bot_app
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set")
        return
    bot_app = Application.builder().token(BOT_TOKEN).build()

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        keyboard = [[InlineKeyboardButton("🛍 Открыть магазин", web_app=WebAppInfo(url=FRONTEND_URL))]]
        await update.message.reply_text(
            f"Привет, {user.first_name}! Добро пожаловать в Мир Косметики ✨",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    bot_app.add_handler(CommandHandler("start", start_cmd))
    await bot_app.initialize()
    await bot_app.start()
    logger.info("Bot started")
    if WEBHOOK_URL:
        await bot_app.bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
        logger.info(f"Webhook set to {WEBHOOK_URL}")

# ─── LIFESPAN ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await start_bot()
    yield
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    await engine.dispose()

# ─── APP ────────────────────────────────────────────────────────────
app = FastAPI(title="Мир Косметики API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# ─── AUTH DEPENDENCY ────────────────────────────────────────────────
async def verify_seller(x_seller_key: Optional[str] = Header(None)):
    if not SELLER_API_KEY or x_seller_key != SELLER_API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid seller key")
    return x_seller_key

# ─── ENDPOINTS ──────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"message": "Мир Косметики API", "status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/products")
async def get_products(session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Product))
    products = result.scalars().all()
    return [{"id": p.id, "name": p.name, "category": p.category, "price": p.price,
             "old_price": p.old_price, "image": p.image, "description": p.description,
             "rating": p.rating, "reviews_count": p.reviews_count} for p in products]

@app.post("/api/v1/orders")
async def create_order(order: OrderCreate, session: AsyncSession = Depends(get_db)):
    db_order = Order(
        buyer_id=order.buyer_id, buyer_name=order.buyer_name,
        buyer_phone=order.buyer_phone, buyer_address=order.buyer_address,
        total=order.total, status="pending",
        delivery_method=order.delivery_method,
        delivery_cost=order.delivery_cost
    )
    session.add(db_order)
    await session.flush()
    for item in order.items:
        session.add(OrderItem(
            order_id=db_order.id, product_id=item.product_id,
            product_name=item.product_name, quantity=item.quantity, price=item.price
        ))
    await session.commit()
    logger.info(f"Order #{db_order.id} created by buyer {order.buyer_id}")
    return {"success": True, "order_id": db_order.id}

@app.get("/api/v1/orders/{buyer_id}")
async def get_orders(buyer_id: str, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(Order).where(Order.buyer_id == buyer_id)
        .order_by(desc(Order.created_at))
        .options(selectinload(Order.items))
    )
    orders = result.scalars().all()
    return [{
        "id": o.id, "buyer_id": o.buyer_id, "buyer_name": o.buyer_name,
        "buyer_phone": o.buyer_phone, "buyer_address": o.buyer_address,
        "status": o.status, "total": o.total,
        "delivery_method": o.delivery_method,
        "delivery_cost": o.delivery_cost,
        "track_number": o.track_number,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "items": [{"product_id": i.product_id, "product_name": i.product_name,
                   "quantity": i.quantity, "price": i.price} for i in o.items]
    } for o in orders]

@app.get("/api/v1/admin/orders")
async def admin_orders(
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_seller)
):
    result = await session.execute(
        select(Order).order_by(desc(Order.created_at)).options(selectinload(Order.items))
    )
    orders = result.scalars().all()
    return [{
        "id": o.id, "buyer_id": o.buyer_id, "buyer_name": o.buyer_name,
        "buyer_phone": o.buyer_phone, "buyer_address": o.buyer_address,
        "status": o.status, "total": o.total,
        "delivery_method": o.delivery_method,
        "delivery_cost": o.delivery_cost,
        "track_number": o.track_number,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "items": [{"product_id": i.product_id, "product_name": i.product_name,
                   "quantity": i.quantity, "price": i.price} for i in o.items]
    } for o in orders]

@app.patch("/api/v1/orders/{order_id}/status")
async def update_status(
    order_id: int,
    update: StatusUpdate,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_seller)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = update.status
    await session.commit()
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
    logger.info(f"Order #{order_id} track updated: {update.track_number}")
    return {"success": True, "order_id": order.id, "track_number": order.track_number}

@app.post("/api/v1/reviews")
async def create_review(review: ReviewCreate, session: AsyncSession = Depends(get_db)):
    db_review = Review(
        product_id=review.product_id, buyer_id=review.buyer_id,
        buyer_name=review.buyer_name, rating=review.rating, text=review.text
    )
    session.add(db_review)
    await session.commit()
    return {"success": True, "review_id": db_review.id}

@app.get("/api/v1/reviews/{product_id}")
async def get_reviews(product_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(Review).where(Review.product_id == product_id).order_by(desc(Review.created_at))
    )
    reviews = result.scalars().all()
    return [{
        "id": r.id, "buyer_id": r.buyer_id, "buyer_name": r.buyer_name,
        "rating": r.rating, "text": r.text,
        "created_at": r.created_at.isoformat() if r.created_at else None
    } for r in reviews]

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not bot_app:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.post("/api/v1/seed")
async def seed_data(session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Product))
    if result.scalars().first():
        return {"message": "Already seeded"}
    
    products = [
        Product(name="Гидрофильное масло", category="Уход за лицом", price=890, old_price=1200,
                image="https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=400",
                description="Глубокое очищение кожи", rating=4.8, reviews_count=124),
        Product(name="Сыворотка с витамином C", category="Уход за лицом", price=1290, old_price=1590,
                image="https://images.unsplash.com/photo-1608248543803-ba4f8c70ae0b?w=400",
                description="Осветление и выравнивание тона", rating=4.9, reviews_count=89),
        Product(name="Матовая помада", category="Макияж", price=650, old_price=890,
                image="https://images.unsplash.com/photo-1586495777744-4413f21062fa?w=400",
                description="Стойкий цвет на 12 часов", rating=4.7, reviews_count=256),
    ]
    for p in products:
        session.add(p)
    await session.commit()
    return {"message": f"Seeded {len(products)} products"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
