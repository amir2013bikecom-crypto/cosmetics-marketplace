import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db, engine, Base, create_tables
from app.models import Category, Product, Order, OrderItem, Review, WishlistItem, PromoCode
from app.config import settings
from pydantic import BaseModel
from typing import List, Optional
import httpx

BOT_TOKEN = settings.BOT_TOKEN
SELLER_KEY = settings.SELLER_API_KEY
SELLERS = [7890854793, 940063562]
MINI_APP_URL = settings.MINI_APP_URL

async def send_telegram_message(chat_id: int, text: str, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"Telegram send error: {e}")

async def notify_sellers_new_order(order_id: int, total: float, items: list):
    items_text = ", ".join([f"{i['product_name']} x{i['quantity']}" for i in items])
    text = f"🛒 <b>Новый заказ #{order_id}</b>\n\nСостав: {items_text}\nСумма: {total} ₽\n\nОткройте Mini App для управления."
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Отправить", "callback_data": f"seller_shipped_{order_id}"},
            {"text": "❌ Отказаться", "callback_data": f"seller_cancelled_{order_id}"}
        ]]
    }
    for sid in SELLERS:
        await send_telegram_message(sid, text, keyboard)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    if settings.API_URL and BOT_TOKEN:
        webhook_url = f"{settings.API_URL}/webhook"
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                json={"url": webhook_url, "secret_token": settings.WEBHOOK_SECRET}
            )
    yield

app = FastAPI(title="Мир Косметики API", version="3.0.0", lifespan=lifespan)

origins = [x.strip() for x in settings.CORS_ORIGINS.split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CartItem(BaseModel):
    product_id: int
    quantity: int = 1

class OrderCreate(BaseModel):
    buyer_id: str
    buyer_name: Optional[str] = ""
    buyer_phone: Optional[str] = ""
    buyer_address: Optional[str] = ""
    items: List[CartItem]
    promo_code: Optional[str] = ""

class StatusUpdate(BaseModel):
    status: str

class ReviewCreate(BaseModel):
    product_id: int
    buyer_id: str
    buyer_name: Optional[str] = ""
    rating: int = 5
    text: str

class WishlistCreate(BaseModel):
    buyer_id: str
    product_id: int

class PromoValidate(BaseModel):
    code: str

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/v1/categories/")
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category))
    rows = result.scalars().all()
    return [{"id": r.id, "name": r.name, "slug": r.slug} for r in rows]

@app.get("/api/v1/products/")
async def list_products(category: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    q = select(Product)
    if category:
        cat = await db.execute(select(Category).where(Category.slug == category))
        c = cat.scalar_one_or_none()
        if c:
            q = q.where(Product.category_id == c.id)
    result = await db.execute(q)
    rows = result.scalars().all()
    out = []
    for r in rows:
        cat = await db.get(Category, r.category_id)
        out.append({
            "id": r.id, "name": r.name, "description": r.description,
            "price": str(r.price), "image_url": r.image_url,
            "stock": r.stock or 0, "is_active": r.is_active if r.is_active is not None else True,
            "category": {"id": cat.id, "name": cat.name, "slug": cat.slug} if cat else None,
            "avg_rating": r.avg_rating, "review_count": r.review_count or 0
        })
    return out

@app.get("/api/v1/products/{product_id}")
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    r = await db.get(Product, product_id)
    if not r:
        raise HTTPException(status_code=404, detail="Товар не найден")
    cat = await db.get(Category, r.category_id)
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "price": str(r.price), "image_url": r.image_url,
        "stock": r.stock or 0, "is_active": r.is_active if r.is_active is not None else True,
        "category": {"id": cat.id, "name": cat.name, "slug": cat.slug} if cat else None,
        "avg_rating": r.avg_rating, "review_count": r.review_count or 0
    }

@app.post("/api/v1/orders/")
async def create_order(data: OrderCreate, db: AsyncSession = Depends(get_db)):
    total = 0.0
    for it in data.items:
        p = await db.get(Product, it.product_id)
        if not p:
            raise HTTPException(status_code=404, detail="Товар не найден: " + str(it.product_id))
        total += float(p.price) * it.quantity

    discount = 0.0
    if data.promo_code:
        pr = await db.execute(select(PromoCode).where(PromoCode.code == data.promo_code.upper(), PromoCode.active == True))
        promo = pr.scalar_one_or_none()
        if promo:
            discount = total * promo.discount_percent / 100.0
            total = max(0.0, total - discount)

    order = Order(
        buyer_id=int(data.buyer_id) if data.buyer_id.isdigit() else 0,
        buyer_name=data.buyer_name or "",
        buyer_phone=data.buyer_phone or "",
        buyer_address=data.buyer_address or "",
        total=round(total, 2),
        status="pending",
        promo_code=data.promo_code.upper() if data.promo_code else ""
    )
    db.add(order)
    await db.flush()

    order_items = []
    for it in data.items:
        p = await db.get(Product, it.product_id)
        item = OrderItem(
            order_id=order.id,
            product_id=it.product_id,
            product_name=p.name if p else "",
            quantity=it.quantity,
            price=p.price if p else 0.0
        )
        db.add(item)
        order_items.append({"product_name": p.name if p else "", "quantity": it.quantity, "price": float(p.price) if p else 0})

    await db.commit()
    await notify_sellers_new_order(order.id, float(order.total), order_items)
    return {"id": order.id, "total": float(order.total), "status": order.status}

@app.get("/api/v1/orders/{buyer_id}")
async def buyer_orders(buyer_id: str, db: AsyncSession = Depends(get_db)):
    bid = int(buyer_id) if buyer_id.isdigit() else 0
    result = await db.execute(select(Order).where(Order.buyer_id == bid).order_by(Order.created_at.desc()))
    rows = result.scalars().all()
    out = []
    for o in rows:
        items_r = await db.execute(select(OrderItem).where(OrderItem.order_id == o.id))
        items = items_r.scalars().all()
        out.append({
            "id": o.id, "total": float(o.total), "status": o.status,
            "buyer_address": o.buyer_address, "buyer_phone": o.buyer_phone,
            "promo_code": o.promo_code,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "items": [{"product_name": i.product_name, "quantity": i.quantity, "price": float(i.price)} for i in items]
        })
    return out

@app.patch("/api/v1/orders/{order_id}/status")
async def update_status(order_id: int, data: StatusUpdate, x_seller_key: str = Header(""), db: AsyncSession = Depends(get_db)):
    if x_seller_key != SELLER_KEY:
        raise HTTPException(status_code=403, detail="Неверный ключ продавца")
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    order.status = data.status
    await db.commit()

    if data.status == "shipped":
        text = f"📦 Ваш заказ #{order_id} отправлен! Ожидайте доставку."
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Получил", "callback_data": f"buyer_received_{order_id}"},
                {"text": "❌ Не получил", "callback_data": f"buyer_notreceived_{order_id}"}
            ]]
        }
        await send_telegram_message(int(order.buyer_id), text, keyboard)
    elif data.status == "cancelled":
        await send_telegram_message(int(order.buyer_id), f"❌ Заказ #{order_id} отменён продавцом.")
    elif data.status == "delivered":
        await send_telegram_message(int(order.buyer_id), f"✅ Заказ #{order_id} доставлен! Спасибо за покупку.")
        for sid in SELLERS:
            await send_telegram_message(sid, f"✅ Заказ #{order_id} доставлен покупателю.")

    return {"id": order.id, "status": order.status}

@app.get("/api/v1/orders/all")
async def all_orders(x_seller_key: str = Header(""), db: AsyncSession = Depends(get_db)):
    if x_seller_key != SELLER_KEY:
        raise HTTPException(status_code=403, detail="Неверный ключ продавца")
    result = await db.execute(select(Order).order_by(Order.created_at.desc()))
    rows = result.scalars().all()
    out = []
    for o in rows:
        items_r = await db.execute(select(OrderItem).where(OrderItem.order_id == o.id))
        items = items_r.scalars().all()
        out.append({
            "id": o.id, "total": float(o.total), "status": o.status,
            "buyer_id": o.buyer_id, "buyer_name": o.buyer_name,
            "buyer_address": o.buyer_address, "buyer_phone": o.buyer_phone,
            "promo_code": o.promo_code,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "items": [{"product_name": i.product_name, "quantity": i.quantity, "price": float(i.price)} for i in items]
        })
    return out

@app.post("/api/v1/reviews/")
async def create_review(data: ReviewCreate, db: AsyncSession = Depends(get_db)):
    p = await db.get(Product, data.product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    review = Review(
        product_id=data.product_id,
        buyer_id=int(data.buyer_id) if data.buyer_id.isdigit() else 0,
        buyer_name=data.buyer_name or "",
        rating=data.rating,
        text=data.text
    )
    db.add(review)
    await db.flush()
    avg_r = await db.execute(select(func.avg(Review.rating)).where(Review.product_id == data.product_id))
    cnt_r = await db.execute(select(func.count(Review.id)).where(Review.product_id == data.product_id))
    p.avg_rating = round(avg_r.scalar() or 0.0, 1)
    p.review_count = cnt_r.scalar() or 0
    await db.commit()
    return {"id": review.id}

@app.get("/api/v1/reviews/{product_id}")
async def product_reviews(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Review).where(Review.product_id == product_id).order_by(Review.created_at.desc()))
    rows = result.scalars().all()
    return [{
        "id": r.id, "buyer_name": r.buyer_name, "rating": r.rating,
        "text": r.text, "created_at": r.created_at.isoformat() if r.created_at else None
    } for r in rows]

@app.post("/api/v1/wishlist/")
async def add_wishlist(data: WishlistCreate, db: AsyncSession = Depends(get_db)):
    bid = int(data.buyer_id) if data.buyer_id.isdigit() else 0
    ex = await db.execute(select(WishlistItem).where(WishlistItem.buyer_id == bid, WishlistItem.product_id == data.product_id))
    if ex.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Уже в избранном")
    db.add(WishlistItem(buyer_id=bid, product_id=data.product_id))
    await db.commit()
    return {"ok": True}

@app.delete("/api/v1/wishlist/{item_id}")
async def remove_wishlist(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(WishlistItem, item_id)
    if item:
        await db.delete(item)
        await db.commit()
    return {"ok": True}

@app.get("/api/v1/wishlist/{buyer_id}")
async def list_wishlist(buyer_id: str, db: AsyncSession = Depends(get_db)):
    bid = int(buyer_id) if buyer_id.isdigit() else 0
    result = await db.execute(select(WishlistItem).where(WishlistItem.buyer_id == bid))
    rows = result.scalars().all()
    out = []
    for w in rows:
        p = await db.get(Product, w.product_id)
        if p:
            out.append({
                "id": w.id,
                "product": {"id": p.id, "name": p.name, "price": str(p.price), "image_url": p.image_url, "stock": p.stock}
            })
    return out

@app.post("/api/v1/promo/validate")
async def validate_promo(data: PromoValidate, db: AsyncSession = Depends(get_db)):
    pr = await db.execute(select(PromoCode).where(PromoCode.code == data.code.upper(), PromoCode.active == True))
    promo = pr.scalar_one_or_none()
    if not promo:
        raise HTTPException(status_code=404, detail="Промокод не найден или неактивен")
    return {"code": promo.code, "discount_percent": promo.discount_percent, "valid": True}

@app.post("/api/v1/seed")
async def seed_data(db: AsyncSession = Depends(get_db)):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    cats = [
        {"name": "Уход за лицом", "slug": "face"},
        {"name": "Уход за телом", "slug": "body"},
        {"name": "Декоративная косметика", "slug": "makeup"},
        {"name": "Ароматы", "slug": "perfume"},
    ]
    for c in cats:
        ex = await db.execute(select(Category).where(Category.slug == c["slug"]))
        if not ex.scalar_one_or_none():
            db.add(Category(name=c["name"], slug=c["slug"]))
    await db.commit()

    cat_map = {}
    cr = await db.execute(select(Category))
    for c in cr.scalars().all():
        cat_map[c.slug] = c.id

    products = [
        {"name": "Крем для лица SPF50", "price": 1290, "category": "face", "image_url": "https://images.unsplash.com/photo-1556228578-8c89e6adf883?w=400", "description": "Увлажняющий крем с защитой от солнца", "stock": 46},
        {"name": "Сыворотка с витамином C", "price": 2490, "category": "face", "image_url": "https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=400", "description": "Антиоксидантная сыворотка", "stock": 29},
        {"name": "Скраб для тела", "price": 890, "category": "body", "image_url": "https://images.unsplash.com/photo-1608248597279-f99d160bfcbc?w=400", "description": "Кофейный скраб с маслом ши", "stock": 39},
        {"name": "Масло для тела", "price": 1190, "category": "body", "image_url": "https://images.unsplash.com/photo-1585652757141-4e462a1ef622?w=400", "description": "Питательное масло с витамином E", "stock": 23},
        {"name": "Помада матовая", "price": 790, "category": "makeup", "image_url": "https://images.unsplash.com/photo-1586495777744-4e6232bf2f86?w=400", "description": "Стойкая помада 24 часа", "stock": 59},
        {"name": "Тушь для ресниц", "price": 690, "category": "makeup", "image_url": "https://images.unsplash.com/photo-1512207736890-6ffed8a84e8d?w=400", "description": "Объём и длина", "stock": 79},
        {"name": "Тональный крем", "price": 1490, "category": "makeup", "image_url": "https://images.unsplash.com/photo-1522338242992-e1a54906a8da?w=400", "description": "Лёгкое покрытие", "stock": 45},
        {"name": "Парфюм Rose Garden", "price": 3990, "category": "perfume", "image_url": "https://images.unsplash.com/photo-1541643600914-78b084683702?w=400", "description": "Цветочный аромат", "stock": 20},
        {"name": "Парфюм Ocean Blue", "price": 2990, "category": "perfume", "image_url": "https://images.unsplash.com/photo-1523293182086-7651a899d37f?w=400", "description": "Свежий морской аромат", "stock": 15},
        {"name": "Мицелярная вода", "price": 1732, "category": "face", "image_url": None, "description": "Мягкое очищение", "stock": 15},
    ]
    for p in products:
        ex = await db.execute(select(Product).where(Product.name == p["name"]))
        if not ex.scalar_one_or_none():
            db.add(Product(
                name=p["name"], description=p["description"], price=p["price"],
                image_url=p["image_url"], category_id=cat_map.get(p["category"]),
                stock=p["stock"], is_active=True
            ))

    promos = [{"code": "SKIDKA10", "discount_percent": 10}, {"code": "HELLO20", "discount_percent": 20}]
    for pr in promos:
        ex = await db.execute(select(PromoCode).where(PromoCode.code == pr["code"]))
        if not ex.scalar_one_or_none():
            db.add(PromoCode(code=pr["code"], discount_percent=pr["discount_percent"], active=True))
    await db.commit()
    return {"detail": "База заполнена"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    body = await request.json()
    if "callback_query" in body:
        cq = body["callback_query"]
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        if data.startswith("seller_shipped_"):
            order_id = data.split("_")[2]
            await send_telegram_message(chat_id, f"✅ Статус заказа #{order_id} обновлён: отправлен")
        elif data.startswith("seller_cancelled_"):
            order_id = data.split("_")[2]
            await send_telegram_message(chat_id, f"❌ Заказ #{order_id} отменён")
        elif data.startswith("buyer_received_"):
            order_id = data.split("_")[2]
            await send_telegram_message(chat_id, f"🎉 Спасибо за подтверждение! Заказ #{order_id} получен.")
        elif data.startswith("buyer_notreceived_"):
            order_id = data.split("_")[2]
            await send_telegram_message(chat_id, f"⚠️ Мы уведомили продавца о проблеме с заказом #{order_id}.")
    elif "message" in body and body["message"].get("text", "").startswith("/start"):
        chat_id = body["message"]["chat"]["id"]
        keyboard = {
            "inline_keyboard": [[
                {"text": "🛍 Открыть магазин", "web_app": {"url": MINI_APP_URL}}
            ]]
        }
        text = "Добро пожаловать в <b>Мир Косметики</b>! Нажмите кнопку ниже, чтобы начать покупки."
        await send_telegram_message(chat_id, text, keyboard)
    return {"ok": True}
