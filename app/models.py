from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, DateTime, Boolean, BigInteger, Numeric
from sqlalchemy.sql import func
from app.database import Base

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    price = Column(Numeric(10, 2), nullable=False)
    image_url = Column(String)
    stock = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    avg_rating = Column(Float, default=0.0)
    review_count = Column(Integer, default=0)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(BigInteger, nullable=False)
    buyer_name = Column(String)
    buyer_phone = Column(String)
    buyer_address = Column(Text)
    total = Column(Numeric(10, 2), nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    promo_code = Column(String)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String)
    quantity = Column(Integer, default=1)
    price = Column(Numeric(10, 2))

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    buyer_id = Column(BigInteger, nullable=False)
    buyer_name = Column(String)
    rating = Column(Integer, default=5)
    text = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(BigInteger, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"))

class PromoCode(Base):
    __tablename__ = "promo_codes"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    discount_percent = Column(Integer, default=10)
    active = Column(Boolean, default=True)
