from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr, validator, field_validator
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
import uuid
import logging
import shutil
import base64
import asyncio
from io import BytesIO
from PIL import Image
import googlemaps
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv
from pathlib import Path
from emergentintegrations.payments.stripe.checkout import StripeCheckout, CheckoutSessionResponse, CheckoutStatusResponse, CheckoutSessionRequest
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.pdfgen import canvas as pdf_canvas

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Database connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="Relivv - Netherlands Marketplace")

# Create uploads directory
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(UPLOAD_DIR)), name="static")

# Mount uploads directory separately for direct access
uploads_path = Path("/app/uploads")
uploads_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory="/app/uploads"), name="uploads")

# Google Maps client initialization
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')
if GOOGLE_MAPS_API_KEY and GOOGLE_MAPS_API_KEY != 'YOUR_GOOGLE_MAPS_API_KEY_HERE':
    try:
        gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
        logging.info("Google Maps client initialized successfully")
    except Exception as e:
        gmaps = None
        logging.warning(f"Google Maps client initialization failed: {str(e)}")
else:
    gmaps = None
    logging.warning("Google Maps API key not configured. Location features will be limited.")

# SendGrid Email Client initialization
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'noreply@relivv.nl')

if SENDGRID_API_KEY and SENDGRID_API_KEY != 'YOUR_SENDGRID_API_KEY_HERE':
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        logging.info("SendGrid client initialized successfully")
    except Exception as e:
        sg = None
        logging.warning(f"SendGrid client initialization failed: {str(e)}")
else:
    sg = None
    logging.warning("SendGrid API key not configured. Email features will be limited.")

# Stripe Payment Client initialization
STRIPE_API_KEY = os.environ.get('STRIPE_API_KEY', 'sk_test_emergent')
stripe_checkout = None

# Frontend URL for email links
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'https://relivv-marketplace-1.preview.emergentagent.com')

security = HTTPBearer()

# Password hashing - Use sha256_crypt to avoid bcrypt issues
pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.environ.get('SECRET_KEY', 'tweedekans-secret-key-change-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# User Models
class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str = Field(..., min_length=2)
    phone: Optional[str] = None
    profile_image: Optional[str] = None
    is_business_seller: bool = Field(default=False, description="Whether user is a business seller")
    business_name: Optional[str] = Field(None, description="Company name (required for business sellers)")
    vat_number: Optional[str] = Field(None, description="BTW/VAT number (required for business sellers)")
    
    @validator('business_name')
    def validate_business_name(cls, v, values):
        if values.get('is_business_seller') and not v:
            raise ValueError('Business name is required for business sellers')
        return v
    
    @validator('vat_number')
    def validate_vat_number(cls, v, values):
        if values.get('is_business_seller') and not v:
            raise ValueError('VAT/BTW number is required for business sellers')
        return v
    
class UserLogin(BaseModel):
    email: EmailStr
    password: str
    
class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: EmailStr
    name: str
    phone: Optional[str] = None
    profile_image: Optional[str] = None
    is_business_seller: bool = Field(default=False)
    business_name: Optional[str] = None
    vat_number: Optional[str] = None
    is_admin: bool = False
    is_banned: bool = False
    is_verified: bool = Field(default=False, description="Profile verification status")
    verification_requested: bool = Field(default=False, description="Verification request pending")
    verification_requested_at: Optional[datetime] = None
    trust_score: float = Field(default=50.0, description="Trust score 0-100")
    completed_transactions: int = Field(default=0, description="Successfully completed transactions")
    cancelled_transactions: int = Field(default=0, description="Cancelled transactions")
    reports_received: int = Field(default=0, description="Number of reports against user")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    rating_average: float = Field(default=0.0, description="Average seller rating")
    rating_count: int = Field(default=0, description="Total number of ratings")
    is_featured_seller: bool = Field(default=False, description="Featured seller status")
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None

# Rating Models
class RatingCreate(BaseModel):
    rated_user_id: str = Field(..., description="ID of user being rated")
    transaction_id: str = Field(..., description="Related transaction ID")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1-5 stars")
    comment: Optional[str] = Field(None, max_length=500, description="Optional review comment")

class Rating(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rater_id: str = Field(..., description="ID of user giving rating")
    rated_user_id: str = Field(..., description="ID of user being rated")
    transaction_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Cart Models
class CartItem(BaseModel):
    product_id: str
    quantity: int = 1
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Cart(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    items: List[CartItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_abandoned: bool = Field(default=False)
    reminder_sent: bool = Field(default=False)

# Email Models
class EmailLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    email_type: str  # welcome, purchase_confirmation, cart_reminder
    recipient_email: str
    subject: str
    status: str = Field(default="pending")  # pending, sent, failed
    sent_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: Optional[str] = None
    
# Product Models
class ProductCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10)
    price: float = Field(..., gt=0)
    category: str
    condition: str = Field(..., pattern="^(excellent|good|fair|poor)$")
    images: List[str] = Field(default_factory=list)
    pickup_address: str = Field(..., description="Pickup address")
    pickup_coordinates: Optional[Dict[str, float]] = None  # {lat, lng}
    is_featured: bool = False
    
# Location Models
class LocationCreate(BaseModel):
    address: str = Field(..., description="Full address string")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
class Location(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    address: str
    latitude: float
    longitude: float
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = Field(default="Netherlands")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Product(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    price: float
    category: str
    condition: str
    images: List[str]
    location_id: str = Field(..., description="Reference to location")
    pickup_location: Optional[Dict[str, Any]] = None  # For Google Maps data
    seller_id: str
    seller_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_sold: bool = False
    views: int = 0
    is_featured: bool = False
    
# Transaction Models  
class TransactionCreate(BaseModel):
    product_id: str
    payment_provider: str = Field(..., pattern="^(stripe|mollie)$")

class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str
    buyer_id: str
    seller_id: str
    amount: float  # Product price (seller receives this)
    commission: float = Field(default=0.0)  # Platform fee (5%)
    commission_rate: float = Field(default=0.05)  # 5% platform fee
    total_amount: float = Field(default=0.0)  # Total paid by buyer (amount + commission)
    status: str = Field(default="pending")  # pending, held, completed, cancelled, refunded
    payment_provider: str  # stripe
    payment_session_id: Optional[str] = None
    delivery_status: str = Field(default="pending")  # pending, delivered, confirmed, disputed
    delivery_confirmed_at: Optional[datetime] = None
    auto_release_at: Optional[datetime] = None  # Automatic release date (7 days after delivery)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None

# Payment Transaction Models (for Stripe integration)
class PaymentTransaction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    transaction_id: str  # Links to Transaction collection
    product_id: str
    buyer_id: str
    seller_id: str
    amount: float
    currency: str = "eur"
    payment_status: str = Field(default="pending")  # pending, paid, failed, expired, refunded
    metadata: Optional[Dict[str, str]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

class DeliveryConfirmation(BaseModel):
    transaction_id: str
    confirmation_type: str = Field(..., pattern="^(delivered|dispute)$")
    notes: Optional[str] = None

# Message Models
class MessageCreate(BaseModel):
    recipient_id: str
    product_id: str
    content: str = Field(..., min_length=1, max_length=1000)
    
class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str
    recipient_id: str
    product_id: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_read: bool = False

class ConversationCreate(BaseModel):
    product_id: str
    recipient_id: str  # The other party (seller if buyer initiates, buyer if seller initiates)
    initial_message: str = Field(..., min_length=1, max_length=1000)

class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str
    buyer_id: str
    seller_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message: str = ""
    last_message_at: Optional[datetime] = None
    buyer_unread_count: int = 0
    seller_unread_count: int = 0
    buyer_typing: bool = False
    seller_typing: bool = False
    buyer_typing_at: Optional[datetime] = None
    seller_typing_at: Optional[datetime] = None

class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    sender_id: str
    message: str
    read: bool = False
    read_at: Optional[datetime] = None
    attachments: list = []  # List of {type: 'image'|'file', url: str, name: str, size: int}
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Support Ticket Models
class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=10, max_length=2000)
    category: str = Field(default="other")  # account, payment, product, technical, other
    priority: str = Field(default="medium")  # low, medium, high, urgent

class Ticket(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    subject: str
    message: str
    category: str
    priority: str
    status: str = "open"  # open, in_progress, resolved, closed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TicketReplyCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)

class TicketReply(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticket_id: str
    user_id: str
    message: str
    is_staff: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TicketStatusUpdate(BaseModel):
    status: str  # open, in_progress, resolved, closed

# Review Models
class ReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)  # 1-5 stars
    comment: Optional[str] = Field(None, max_length=500)

class Review(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str
    user_id: str
    seller_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Wishlist Models
class Wishlist(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    product_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Notification Models
class NotificationCreate(BaseModel):
    type: str  # order, message, review, support, system
    title: str
    message: str
    link: Optional[str] = None

class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    type: str  # order, message, review, support, system, sale, favorite, price_drop
    title: str
    message: str
    link: Optional[str] = None
    read: bool = False
    icon: Optional[str] = None  # emoji icon for notification type
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class NotificationPreferences(BaseModel):
    user_id: str
    email_notifications: bool = True
    notification_types: dict = {
        "order": True,
        "message": True,
        "review": True,
        "support": True,
        "system": True,
        "sale": True,
        "favorite": True,
        "price_drop": False
    }
    quiet_hours: dict = {
        "enabled": False,
        "start": "22:00",
        "end": "08:00"
    }
    digest_enabled: bool = False
    digest_frequency: str = "daily"  # daily, weekly
    sound_enabled: bool = True

# Report Models
class ReportCreate(BaseModel):
    reported_type: str = Field(..., description="Type: user, product, message")
    reported_id: str = Field(..., description="ID of reported entity")
    reason: str = Field(..., description="spam, fraud, inappropriate, other")
    description: str = Field(..., min_length=10, max_length=500, description="Detailed description")

class Report(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    reporter_id: str
    reported_type: str  # user, product, message
    reported_id: str
    reason: str  # spam, fraud, inappropriate, scam, fake, other
    description: str
    status: str = "pending"  # pending, reviewed, resolved, dismissed
    admin_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Verification Request Model
class VerificationRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    documents: list = []  # List of document URLs
    phone_verified: bool = False
    email_verified: bool = False
    status: str = "pending"  # pending, approved, rejected
    admin_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Invoice Models
class Invoice(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: str  # Generated invoice number (e.g., INV-2025-00001)
    transaction_id: str
    buyer_id: str
    seller_id: str
    product_id: str
    invoice_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amount: float  # Product price
    commission: float  # Platform commission (5%)
    vat_amount: float = Field(default=0.0)  # VAT amount (21% in Netherlands)
    vat_rate: float = Field(default=0.21)  # VAT rate
    total_amount: float  # Total amount paid
    payment_method: str = "stripe"  # Payment method used
    payment_status: str  # paid, pending, refunded
    invoice_status: str = "issued"  # issued, paid, cancelled, refunded
    pdf_url: Optional[str] = None  # URL to generated PDF
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Helper functions
async def create_notification(user_id: str, notification_type: str, title: str, message: str, link: str = None, icon: str = None):
    """Helper function to create a notification"""
    # Check user preferences
    prefs = await db.notification_preferences.find_one({"user_id": user_id})
    
    # If preferences exist and this notification type is disabled, skip
    if prefs and not prefs.get("notification_types", {}).get(notification_type, True):
        return None
    
    # Create notification
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        message=message,
        link=link,
        icon=icon or get_notification_icon(notification_type)
    )
    
    notification_dict = notification.dict()
    notification_dict["created_at"] = notification_dict["created_at"].isoformat()
    
    await db.notifications.insert_one(notification_dict)
    return notification

def get_notification_icon(notification_type: str) -> str:
    """Get icon emoji for notification type"""
    icons = {
        "order": "📦",
        "message": "💬",
        "review": "⭐",
        "support": "🎫",
        "system": "🔔",
        "sale": "💰",
        "favorite": "❤️",
        "price_drop": "💸"
    }
    return icons.get(notification_type, "🔔")

async def calculate_trust_score(user_id: str) -> float:
    """Calculate user trust score based on multiple factors"""
    user = await db.users.find_one({"id": user_id})
    if not user:
        return 50.0
    
    score = 50.0  # Base score
    
    # Completed transactions (+20 max, 2 points per transaction up to 10)
    completed = user.get("completed_transactions", 0)
    score += min(completed * 2, 20)
    
    # Rating average (+15 max)
    rating_avg = user.get("rating_average", 0)
    if rating_avg > 0:
        score += (rating_avg / 5.0) * 15
    
    # Verification status (+10)
    if user.get("is_verified", False):
        score += 10
    
    # Account age (+10 max, 1 point per month up to 10 months)
    account_age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(user["created_at"])).days
    account_age_months = account_age_days / 30
    score += min(account_age_months, 10)
    
    # Penalties
    # Cancelled transactions (-2 per cancellation)
    cancelled = user.get("cancelled_transactions", 0)
    score -= cancelled * 2
    
    # Reports received (-5 per report)
    reports = user.get("reports_received", 0)
    score -= reports * 5
    
    # Ensure score is between 0 and 100
    score = max(0, min(100, score))
    
    # Update user's trust score
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"trust_score": round(score, 1)}}
    )
    
    return round(score, 1)

def get_trust_badge(score: float) -> str:
    """Get trust badge emoji based on score"""
    if score >= 90:
        return "🌟"
    elif score >= 75:
        return "⭐"
    elif score >= 50:
        return "✓"
    else:
        return "⚠️"

async def generate_invoice_number() -> str:
    """Generate unique invoice number"""
    year = datetime.now(timezone.utc).year
    # Count invoices this year
    count = await db.invoices.count_documents({
        "invoice_number": {"$regex": f"^INV-{year}-"}
    })
    return f"INV-{year}-{str(count + 1).zfill(5)}"

async def generate_invoice_pdf(invoice_id: str) -> str:
    """Generate PDF invoice and return the file path"""
    # Get invoice data
    invoice = await db.invoices.find_one({"id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Get related data
    transaction = await db.transactions.find_one({"id": invoice["transaction_id"]})
    buyer = await db.users.find_one({"id": invoice["buyer_id"]})
    seller = await db.users.find_one({"id": invoice["seller_id"]})
    product = await db.products.find_one({"id": invoice["product_id"]})
    
    if not all([transaction, buyer, seller, product]):
        raise HTTPException(status_code=404, detail="Related data not found")
    
    # Create invoice PDF
    pdf_filename = f"invoice_{invoice['invoice_number'].replace('-', '_')}.pdf"
    pdf_path = UPLOAD_DIR / pdf_filename
    
    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2563eb'),
        spaceAfter=30
    )
    elements.append(Paragraph("INVOICE", title_style))
    elements.append(Spacer(1, 0.2*inch))
    
    # Invoice Info
    invoice_info_data = [
        ['Invoice Number:', invoice['invoice_number']],
        ['Invoice Date:', datetime.fromisoformat(invoice['invoice_date']).strftime('%B %d, %Y')],
        ['Transaction ID:', invoice['transaction_id'][:12] + '...'],
        ['Payment Status:', invoice['payment_status'].upper()],
    ]
    
    invoice_info_table = Table(invoice_info_data, colWidths=[2*inch, 3*inch])
    invoice_info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#374151')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(invoice_info_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Buyer and Seller Info
    party_data = [
        ['BILL TO:', 'SELLER:'],
        [buyer.get('name', 'N/A'), seller.get('name', 'N/A')],
        [buyer.get('email', 'N/A'), seller.get('email', 'N/A')],
        [buyer.get('phone', 'N/A'), seller.get('phone', 'N/A')],
    ]
    
    party_table = Table(party_data, colWidths=[2.5*inch, 2.5*inch])
    party_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#6b7280')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
    ]))
    elements.append(party_table)
    elements.append(Spacer(1, 0.4*inch))
    
    # Product/Service Details
    elements.append(Paragraph("ITEMS", styles['Heading2']))
    elements.append(Spacer(1, 0.1*inch))
    
    items_data = [
        ['Description', 'Qty', 'Unit Price', 'Amount'],
        [product.get('title', 'Product'), '1', f"€{invoice['amount']:.2f}", f"€{invoice['amount']:.2f}"],
    ]
    
    items_table = Table(items_data, colWidths=[3*inch, 0.7*inch, 1.2*inch, 1.2*inch])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Totals
    subtotal = invoice['amount']
    commission = invoice['commission']
    vat = invoice.get('vat_amount', 0)
    total = invoice['total_amount']
    
    totals_data = [
        ['Subtotal:', f"€{subtotal:.2f}"],
        ['Platform Commission (5%):', f"€{commission:.2f}"],
        ['VAT (21%):', f"€{vat:.2f}"],
        ['', ''],
        ['TOTAL:', f"€{total:.2f}"],
    ]
    
    totals_table = Table(totals_data, colWidths=[4*inch, 2*inch])
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 2), 'Helvetica'),
        ('FONTNAME', (0, 4), (-1, 4), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 2), 9),
        ('FONTSIZE', (0, 4), (-1, 4), 12),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('TEXTCOLOR', (0, 0), (-1, 2), colors.HexColor('#374151')),
        ('TEXTCOLOR', (0, 4), (-1, 4), colors.HexColor('#1f2937')),
        ('LINEABOVE', (0, 4), (-1, 4), 2, colors.HexColor('#2563eb')),
        ('TOPPADDING', (0, 4), (-1, 4), 10),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.5*inch))
    
    # Payment Info
    payment_info_style = ParagraphStyle(
        'PaymentInfo',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.HexColor('#6b7280'),
        spaceAfter=5
    )
    elements.append(Paragraph(f"<b>Payment Method:</b> {invoice['payment_method'].upper()}", payment_info_style))
    elements.append(Paragraph(f"<b>Payment Date:</b> {datetime.fromisoformat(invoice['invoice_date']).strftime('%B %d, %Y')}", payment_info_style))
    elements.append(Spacer(1, 0.3*inch))
    
    # Footer
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#9ca3af'),
        alignment=1  # Center
    )
    elements.append(Paragraph("Thank you for using Relivv Marketplace!", footer_style))
    elements.append(Paragraph("For support, contact us at support@relivv.nl", footer_style))
    
    # Build PDF
    doc.build(elements)
    
    # Return relative URL
    return f"/static/{pdf_filename}"

def verify_password(plain_password, hashed_password):
    # Truncate password to 72 bytes for bcrypt compatibility
    return pwd_context.verify(plain_password[:72], hashed_password)

def get_password_hash(password):
    # Truncate password to 72 bytes for bcrypt compatibility
    return pwd_context.hash(password[:72])

def create_access_token(data: dict):
    from datetime import timedelta
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = data.copy()
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await db.users.find_one({"id": user_id})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        # Check if user is banned
        if user.get("is_banned", False):
            raise HTTPException(status_code=403, detail="Your account has been banned")
        
        # Try to create User object, if it fails, return the raw dict
        try:
            return User(**user)
        except Exception:
            # If User creation fails, return a simple dict with required fields
            return {
                "id": user.get("id"),
                "email": user.get("email"),
                "name": user.get("name"),
                "phone": user.get("phone")
            }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_admin_user(current_user: User = Depends(get_current_user)):
    """Verify user has admin privileges"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# File Upload Helper Functions
def validate_image(file: UploadFile) -> bool:
    """Validate uploaded image file"""
    # Check file type
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        return False
    
    # Check file size (max 5MB)
    if file.size and file.size > 5 * 1024 * 1024:
        return False
    
    return True

# Google Maps Helper Functions
def geocode_address(address: str) -> Optional[Dict[str, Any]]:
    """Geocode address to coordinates using Google Maps API"""
    if not gmaps:
        return None
    
    try:
        # Add Netherlands bias for better accuracy
        geocode_result = gmaps.geocode(
            address,
            region='NL',
            components={'country': 'NL'}
        )
        
        if geocode_result:
            result = geocode_result[0]
            geometry = result['geometry']['location']
            
            # Extract address components
            components = {}
            for component in result.get('address_components', []):
                types = component['types']
                if 'locality' in types:
                    components['city'] = component['long_name']
                elif 'postal_code' in types:
                    components['postal_code'] = component['long_name']
                elif 'country' in types:
                    components['country'] = component['long_name']
            
            return {
                'latitude': geometry['lat'],
                'longitude': geometry['lng'],
                'formatted_address': result['formatted_address'],
                'components': components,
                'place_id': result.get('place_id')
            }
    except Exception as e:
        logging.error(f"Geocoding error: {str(e)}")
    
    return None

def reverse_geocode_coordinates(lat: float, lng: float) -> Optional[str]:
    """Convert coordinates to address string"""
    if not gmaps:
        return None
    
    try:
        reverse_geocode_result = gmaps.reverse_geocode((lat, lng))
        if reverse_geocode_result:
            return reverse_geocode_result[0]['formatted_address']
    except Exception as e:
        logging.error(f"Reverse geocoding error: {str(e)}")
    
    return None

def find_nearby_locations(lat: float, lng: float, radius_km: float = 5.0) -> List[str]:
    """Find nearby places using coordinates and radius"""
    if not gmaps:
        return []
    
    try:
        # Convert km to meters for Places API
        radius_meters = int(radius_km * 1000)
        
        nearby_result = gmaps.places_nearby(
            location=(lat, lng),
            radius=radius_meters,
            type='point_of_interest'
        )
        
        places = []
        for place in nearby_result.get('results', []):
            places.append({
                'name': place['name'],
                'place_id': place['place_id'],
                'location': place['geometry']['location'],
                'types': place.get('types', [])
            })
        
        return places
    except Exception as e:
        logging.error(f"Nearby search error: {str(e)}")
    
    return []

# Email Templates and Functions
def get_welcome_email_template(user_name: str, language: str = 'nl') -> Dict[str, str]:
    """Generate welcome email template"""
    if language == 'en':
        return {
            "subject": "🎉 Welcome to Relivv - Start Your Circular Journey!",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">✨ Welcome to Relivv!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Netherlands' Leading Circular Marketplace</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hi {user_name}! 👋</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Welcome to Relivv! We're excited to have you join our sustainable community. 
                        You're now part of the circular economy revolution in the Netherlands! 🇳🇱
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0;">
                        <h3 style="color: #1e293b; margin-top: 0;">🚀 What you can do now:</h3>
                        <ul style="color: #64748b; line-height: 1.8;">
                            <li>🔍 <strong>Explore treasures</strong> - Browse unique second-hand items</li>
                            <li>💰 <strong>Start selling</strong> - List your unused items and earn money</li>
                            <li>⭐ <strong>Build your reputation</strong> - Get 5-star ratings from happy buyers</li>
                            <li>📱 <strong>Download our app</strong> - Shop on the go!</li>
                        </ul>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/browse" 
                           style="background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           🔍 Start Exploring
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        🌱 Together we build a sustainable future • Only 5% commission on sales
                    </p>
                </body>
            </html>
            """
        }
    else:  # Dutch
        return {
            "subject": "🎉 Welkom bij Relivv - Begin je Circulaire Reis!",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">✨ Welkom bij Relivv!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Nederlandse Circulaire Marktplaats</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hallo {user_name}! 👋</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Welkom bij Relivv! We zijn blij dat je deel uitmaakt van onze duurzame gemeenschap. 
                        Je bent nu onderdeel van de circulaire economie revolutie in Nederland! 🇳🇱
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0;">
                        <h3 style="color: #1e293b; margin-top: 0;">🚀 Wat je nu kunt doen:</h3>
                        <ul style="color: #64748b; line-height: 1.8;">
                            <li>🔍 <strong>Ontdek schatten</strong> - Blader door unieke tweedehands items</li>
                            <li>💰 <strong>Begin met verkopen</strong> - Plaats je ongebruikte spullen en verdien geld</li>
                            <li>⭐ <strong>Bouw je reputatie op</strong> - Krijg 5-sterren beoordelingen van tevreden kopers</li>
                            <li>📱 <strong>Download onze app</strong> - Shop onderweg!</li>
                        </ul>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/browse" 
                           style="background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           🔍 Begin met Ontdekken
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        🌱 Samen bouwen we aan een duurzame toekomst • Slechts 5% commissie op verkopen
                    </p>
                </body>
            </html>
            """
        }

def get_purchase_confirmation_template(user_name: str, product_title: str, amount: float, language: str = 'nl') -> Dict[str, str]:
    """Generate purchase confirmation email template"""
    if language == 'en':
        return {
            "subject": f"🎉 Purchase Confirmed - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">🎉 Purchase Confirmed!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Your order is being processed</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hi {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Great news! Your purchase has been confirmed and payment is securely held in escrow.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #22c55e;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Order Details:</h3>
                        <p style="margin: 10px 0;"><strong>Product:</strong> {product_title}</p>
                        <p style="margin: 10px 0;"><strong>Amount:</strong> €{amount:.2f}</p>
                        <p style="margin: 10px 0;"><strong>Status:</strong> Payment in Escrow</p>
                    </div>
                    
                    <div style="background: #fef3c7; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #92400e; margin-top: 0;">⏳ What happens next?</h4>
                        <ol style="color: #92400e; line-height: 1.6;">
                            <li>Seller will be notified of your purchase</li>
                            <li>Arrange pickup/delivery with the seller</li>
                            <li>Once confirmed, payment will be released (minus 5% commission)</li>
                        </ol>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/orders" 
                           style="background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           📋 View Order Details
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        Questions? Contact us anytime • Secure payments with buyer protection
                    </p>
                </body>
            </html>
            """
        }
    else:  # Dutch
        return {
            "subject": f"🎉 Aankoop Bevestigd - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">🎉 Aankoop Bevestigd!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Je bestelling wordt verwerkt</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hallo {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Goed nieuws! Je aankoop is bevestigd en de betaling wordt veilig in escrow gehouden.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #22c55e;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Bestelling Details:</h3>
                        <p style="margin: 10px 0;"><strong>Product:</strong> {product_title}</p>
                        <p style="margin: 10px 0;"><strong>Bedrag:</strong> €{amount:.2f}</p>
                        <p style="margin: 10px 0;"><strong>Status:</strong> Betaling in Escrow</p>
                    </div>
                    
                    <div style="background: #fef3c7; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #92400e; margin-top: 0;">⏳ Wat gebeurt er nu?</h4>
                        <ol style="color: #92400e; line-height: 1.6;">
                            <li>De verkoper wordt op de hoogte gesteld van je aankoop</li>
                            <li>Regel ophaling/levering met de verkoper</li>
                            <li>Na bevestiging wordt de betaling vrijgegeven (minus 5% commissie)</li>
                        </ol>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/orders" 
                           style="background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           📋 Bekijk Bestelling Details
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        Vragen? Neem altijd contact met ons op • Veilige betalingen met koperbescherming
                    </p>
                </body>
            </html>
            """
        }

def get_cart_reminder_template(user_name: str, cart_items: List[str], language: str = 'nl') -> Dict[str, str]:
    """Generate abandoned cart reminder email template"""
    items_list = "".join([f"<li>{item}</li>" for item in cart_items])
    
    if language == 'en':
        return {
            "subject": "🛒 Don't forget your treasures - Complete your purchase!",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">🛒 Your Cart is Waiting!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Don't miss out on these amazing finds</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hi {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        We noticed you left some amazing treasures in your cart. 
                        These unique items won't stay available forever!
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #f59e0b;">
                        <h3 style="color: #1e293b; margin-top: 0;">🛍️ Items in your cart:</h3>
                        <ul style="color: #64748b; line-height: 1.6;">
                            {items_list}
                        </ul>
                    </div>
                    
                    <div style="background: #fef3c7; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #92400e; margin-top: 0;">⚡ Why complete your purchase now?</h4>
                        <ul style="color: #92400e; line-height: 1.6;">
                            <li>🔒 Secure escrow protection</li>
                            <li>⭐ Verified sellers with great ratings</li>
                            <li>🚚 Fast and reliable pickup/delivery</li>
                            <li>🌱 Support the circular economy</li>
                        </ul>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/cart" 
                           style="background: linear-gradient(135deg, #f59e0b, #d97706); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           🛒 Complete Your Purchase
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        🎯 Limited availability • Secure payments • 5% commission
                    </p>
                </body>
            </html>
            """
        }
    else:  # Dutch
        return {
            "subject": "🛒 Vergeet je schatten niet - Voltooi je aankoop!",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">🛒 Je Winkelwagen Wacht!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Mis deze geweldige vondsten niet</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hallo {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        We merkten op dat je geweldige schatten in je winkelwagen hebt laten liggen. 
                        Deze unieke items blijven niet voor altijd beschikbaar!
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #f59e0b;">
                        <h3 style="color: #1e293b; margin-top: 0;">🛍️ Items in je winkelwagen:</h3>
                        <ul style="color: #64748b; line-height: 1.6;">
                            {items_list}
                        </ul>
                    </div>
                    
                    <div style="background: #fef3c7; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #92400e; margin-top: 0;">⚡ Waarom nu je aankoop voltooien?</h4>
                        <ul style="color: #92400e; line-height: 1.6;">
                            <li>🔒 Veilige escrow bescherming</li>
                            <li>⭐ Geverifieerde verkopers met geweldige beoordelingen</li>
                            <li>🚚 Snelle en betrouwbare ophaling/levering</li>
                            <li>🌱 Ondersteun de circulaire economie</li>
                        </ul>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/cart" 
                           style="background: linear-gradient(135deg, #f59e0b, #d97706); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           🛒 Voltooi Je Aankoop
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        🎯 Beperkte beschikbaarheid • Veilige betalingen • 5% commissie
                    </p>
                </body>
            </html>
            """
        }

def get_order_confirmation_template(user_name: str, product_title: str, order_id: str, amount: float, language: str = 'nl') -> Dict[str, str]:
    """Generate order confirmation email template for buyer"""
    if language == 'en':
        return {
            "subject": f"🎉 Order Confirmed - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">🎉 Order Confirmed!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Thank you for your purchase</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hi {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Great news! Your order has been confirmed and the seller has been notified.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #6366f1;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Order Details</h3>
                        <p style="color: #64748b; line-height: 1.8; margin: 5px 0;">
                            <strong>Product:</strong> {product_title}<br>
                            <strong>Order ID:</strong> {order_id}<br>
                            <strong>Amount:</strong> €{amount:.2f}
                        </p>
                    </div>
                    
                    <div style="background: #dbeafe; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #1e40af; margin-top: 0;">🔒 What happens next?</h4>
                        <ol style="color: #1e40af; line-height: 1.8;">
                            <li>The seller will contact you to arrange pickup/delivery</li>
                            <li>Payment is held in escrow for 5 days</li>
                            <li>After receiving the item, confirm delivery</li>
                            <li>Payment is released to the seller</li>
                        </ol>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/orders" 
                           style="background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; margin-right: 10px;">
                           📦 View Order
                        </a>
                        <a href="{FRONTEND_URL}/messages" 
                           style="background: white; color: #6366f1; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; border: 2px solid #6366f1;">
                           💬 Message Seller
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        🎯 Secure escrow • Buyer protection • Support available 24/7
                    </p>
                </body>
            </html>
            """
        }
    else:  # Dutch
        return {
            "subject": f"🎉 Bestelling Bevestigd - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">🎉 Bestelling Bevestigd!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Bedankt voor je aankoop</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hallo {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Goed nieuws! Je bestelling is bevestigd en de verkoper is op de hoogte gebracht.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #6366f1;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Bestelgegevens</h3>
                        <p style="color: #64748b; line-height: 1.8; margin: 5px 0;">
                            <strong>Product:</strong> {product_title}<br>
                            <strong>Bestelling ID:</strong> {order_id}<br>
                            <strong>Bedrag:</strong> €{amount:.2f}
                        </p>
                    </div>
                    
                    <div style="background: #dbeafe; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #1e40af; margin-top: 0;">🔒 Wat gebeurt er nu?</h4>
                        <ol style="color: #1e40af; line-height: 1.8;">
                            <li>De verkoper neemt contact met je op voor ophaling/levering</li>
                            <li>Betaling wordt 5 dagen in escrow gehouden</li>
                            <li>Na ontvangst van het item, bevestig levering</li>
                            <li>Betaling wordt vrijgegeven aan de verkoper</li>
                        </ol>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/orders" 
                           style="background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; margin-right: 10px;">
                           📦 Bekijk Bestelling
                        </a>
                        <a href="{FRONTEND_URL}/messages" 
                           style="background: white; color: #6366f1; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; border: 2px solid #6366f1;">
                           💬 Bericht Verkoper
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        🎯 Veilige escrow • Koperbescherming • Ondersteuning 24/7
                    </p>
                </body>
            </html>
            """
        }

def get_seller_notification_template(seller_name: str, buyer_name: str, product_title: str, order_id: str, amount: float, language: str = 'nl') -> Dict[str, str]:
    """Generate seller notification email template"""
    if language == 'en':
        return {
            "subject": f"💰 New Sale - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">💰 Congratulations!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">You made a sale</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hi {seller_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Great news! <strong>{buyer_name}</strong> has purchased your product.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #10b981;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Sale Details</h3>
                        <p style="color: #64748b; line-height: 1.8; margin: 5px 0;">
                            <strong>Product:</strong> {product_title}<br>
                            <strong>Buyer:</strong> {buyer_name}<br>
                            <strong>Order ID:</strong> {order_id}<br>
                            <strong>Sale Amount:</strong> €{amount:.2f}<br>
                            <strong>Your Earnings:</strong> €{(amount * 0.95):.2f} <span style="color: #6b7280;">(after 5% fee)</span>
                        </p>
                    </div>
                    
                    <div style="background: #d1fae5; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #065f46; margin-top: 0;">📋 Next Steps:</h4>
                        <ol style="color: #065f46; line-height: 1.8;">
                            <li>Contact the buyer to arrange pickup/delivery</li>
                            <li>Provide the item in the described condition</li>
                            <li>Buyer confirms delivery within 5 days</li>
                            <li>Payment is released to your account</li>
                        </ol>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/orders" 
                           style="background: linear-gradient(135deg, #10b981, #059669); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; margin-right: 10px;">
                           📦 View Order
                        </a>
                        <a href="{FRONTEND_URL}/messages" 
                           style="background: white; color: #10b981; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; border: 2px solid #10b981;">
                           💬 Message Buyer
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        💸 Secure escrow • Fast payouts • Seller protection
                    </p>
                </body>
            </html>
            """
        }
    else:  # Dutch
        return {
            "subject": f"💰 Nieuwe Verkoop - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">💰 Gefeliciteerd!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Je hebt een verkoop gedaan</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hallo {seller_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        Goed nieuws! <strong>{buyer_name}</strong> heeft je product gekocht.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #10b981;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Verkoopgegevens</h3>
                        <p style="color: #64748b; line-height: 1.8; margin: 5px 0;">
                            <strong>Product:</strong> {product_title}<br>
                            <strong>Koper:</strong> {buyer_name}<br>
                            <strong>Bestelling ID:</strong> {order_id}<br>
                            <strong>Verkoopbedrag:</strong> €{amount:.2f}<br>
                            <strong>Jouw Verdiensten:</strong> €{(amount * 0.95):.2f} <span style="color: #6b7280;">(na 5% vergoeding)</span>
                        </p>
                    </div>
                    
                    <div style="background: #d1fae5; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #065f46; margin-top: 0;">📋 Volgende Stappen:</h4>
                        <ol style="color: #065f46; line-height: 1.8;">
                            <li>Neem contact op met de koper voor ophaling/levering</li>
                            <li>Lever het item in de beschreven staat</li>
                            <li>Koper bevestigt levering binnen 5 dagen</li>
                            <li>Betaling wordt vrijgegeven aan je account</li>
                        </ol>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/orders" 
                           style="background: linear-gradient(135deg, #10b981, #059669); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; margin-right: 10px;">
                           📦 Bekijk Bestelling
                        </a>
                        <a href="{FRONTEND_URL}/messages" 
                           style="background: white; color: #10b981; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block; border: 2px solid #10b981;">
                           💬 Bericht Koper
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        💸 Veilige escrow • Snelle uitbetalingen • Verkopersbescherming
                    </p>
                </body>
            </html>
            """
        }

def get_delivery_confirmation_template(user_name: str, product_title: str, order_id: str, language: str = 'nl') -> Dict[str, str]:
    """Generate delivery confirmation email template"""
    if language == 'en':
        return {
            "subject": f"✅ Delivery Confirmed - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">✅ Delivery Confirmed!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Payment has been released</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hi {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        The buyer has confirmed delivery of <strong>{product_title}</strong> and the payment has been released from escrow.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #3b82f6;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Transaction Complete</h3>
                        <p style="color: #64748b; line-height: 1.8; margin: 5px 0;">
                            <strong>Product:</strong> {product_title}<br>
                            <strong>Order ID:</strong> {order_id}<br>
                            <strong>Status:</strong> ✅ Delivered
                        </p>
                    </div>
                    
                    <div style="background: #dbeafe; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #1e40af; margin-top: 0;">🎉 What's Next?</h4>
                        <p style="color: #1e40af; line-height: 1.8;">
                            Your funds are now available in your account. Thank you for using Relivv for your sale!
                            We'd love to hear your feedback about this transaction.
                        </p>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/sell" 
                           style="background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           🚀 List Another Item
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        ⭐ Thank you for being part of the Relivv community
                    </p>
                </body>
            </html>
            """
        }
    else:  # Dutch
        return {
            "subject": f"✅ Levering Bevestigd - {product_title}",
            "html_content": f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); padding: 40px 20px; text-align: center; border-radius: 15px; margin-bottom: 30px;">
                        <h1 style="color: white; margin: 0; font-size: 28px;">✅ Levering Bevestigd!</h1>
                        <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Betaling is vrijgegeven</p>
                    </div>
                    
                    <h2 style="color: #1e293b;">Hallo {user_name}!</h2>
                    
                    <p style="color: #64748b; line-height: 1.6;">
                        De koper heeft de levering van <strong>{product_title}</strong> bevestigd en de betaling is vrijgegeven uit escrow.
                    </p>
                    
                    <div style="background: #f8fafc; padding: 25px; border-radius: 12px; margin: 25px 0; border-left: 4px solid #3b82f6;">
                        <h3 style="color: #1e293b; margin-top: 0;">📦 Transactie Voltooid</h3>
                        <p style="color: #64748b; line-height: 1.8; margin: 5px 0;">
                            <strong>Product:</strong> {product_title}<br>
                            <strong>Bestelling ID:</strong> {order_id}<br>
                            <strong>Status:</strong> ✅ Geleverd
                        </p>
                    </div>
                    
                    <div style="background: #dbeafe; padding: 20px; border-radius: 12px; margin: 25px 0;">
                        <h4 style="color: #1e40af; margin-top: 0;">🎉 Wat Nu?</h4>
                        <p style="color: #1e40af; line-height: 1.8;">
                            Je geld is nu beschikbaar op je account. Bedankt voor het gebruik van Relivv voor je verkoop!
                            We horen graag je feedback over deze transactie.
                        </p>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{FRONTEND_URL}/sell" 
                           style="background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; text-decoration: none; padding: 15px 30px; border-radius: 12px; font-weight: bold; display: inline-block;">
                           🚀 Nog Een Item Aanbieden
                        </a>
                    </div>
                    
                    <p style="color: #94a3b8; font-size: 14px; text-align: center; margin-top: 40px;">
                        ⭐ Bedankt dat je deel uitmaakt van de Relivv community
                    </p>
                </body>
            </html>
            """
        }

async def send_email(recipient_email: str, subject: str, html_content: str, user_id: str, email_type: str):
    """Send email using SendGrid and log the attempt"""
    if not sg:
        logging.warning("SendGrid not configured, email not sent")
        return False
    
    try:
        message = Mail(
            from_email=SENDER_EMAIL,
            to_emails=recipient_email,
            subject=subject,
            html_content=html_content
        )
        
        response = sg.send(message)
        
        # Log email attempt
        email_log = EmailLog(
            user_id=user_id,
            email_type=email_type,
            recipient_email=recipient_email,
            subject=subject,
            status="sent" if response.status_code == 202 else "failed",
            sent_at=datetime.now(timezone.utc)
        )
        
        email_log_dict = email_log.dict()
        email_log_dict["created_at"] = email_log_dict["created_at"].isoformat()
        email_log_dict["sent_at"] = email_log_dict["sent_at"].isoformat() if email_log_dict["sent_at"] else None
        
        await db.email_logs.insert_one(email_log_dict)
        
        return response.status_code == 202
        
    except Exception as e:
        logging.error(f"Email send error: {str(e)}")
        
        # Log failed email attempt
        email_log = EmailLog(
            user_id=user_id,
            email_type=email_type,
            recipient_email=recipient_email,
            subject=subject,
            status="failed",
            error_message=str(e)
        )
        
        email_log_dict = email_log.dict()
        email_log_dict["created_at"] = email_log_dict["created_at"].isoformat()
        
        await db.email_logs.insert_one(email_log_dict)
        
        return False

async def send_welcome_email(user: User, language: str = 'nl'):
    """Send welcome email to new user"""
    template = get_welcome_email_template(user.name, language)
    return await send_email(
        recipient_email=user.email,
        subject=template["subject"],
        html_content=template["html_content"],
        user_id=user.id,
        email_type="welcome"
    )

async def send_purchase_confirmation_email(user: User, product_title: str, amount: float, language: str = 'nl'):
    """Send purchase confirmation email"""
    template = get_purchase_confirmation_template(user.name, product_title, amount, language)
    return await send_email(
        recipient_email=user.email,
        subject=template["subject"],
        html_content=template["html_content"],
        user_id=user.id,
        email_type="purchase_confirmation"
    )

async def send_cart_reminder_email(user: User, cart_items: List[str], language: str = 'nl'):
    """Send abandoned cart reminder email"""
    template = get_cart_reminder_template(user.name, cart_items, language)
    return await send_email(
        recipient_email=user.email,
        subject=template["subject"],
        html_content=template["html_content"],
        user_id=user.id,
        email_type="cart_reminder"
    )

async def send_order_confirmation_email(user: User, product_title: str, order_id: str, amount: float, language: str = 'nl'):
    """Send order confirmation email to buyer"""
    template = get_order_confirmation_template(user.name, product_title, order_id, amount, language)
    return await send_email(
        recipient_email=user.email,
        subject=template["subject"],
        html_content=template["html_content"],
        user_id=user.id,
        email_type="order_confirmation"
    )

async def send_seller_notification_email(seller: User, buyer_name: str, product_title: str, order_id: str, amount: float, language: str = 'nl'):
    """Send new sale notification email to seller"""
    template = get_seller_notification_template(seller.name, buyer_name, product_title, order_id, amount, language)
    return await send_email(
        recipient_email=seller.email,
        subject=template["subject"],
        html_content=template["html_content"],
        user_id=seller.id,
        email_type="seller_notification"
    )

async def send_delivery_confirmation_email(seller: User, product_title: str, order_id: str, language: str = 'nl'):
    """Send delivery confirmation email to seller"""
    template = get_delivery_confirmation_template(seller.name, product_title, order_id, language)
    return await send_email(
        recipient_email=seller.email,
        subject=template["subject"],
        html_content=template["html_content"],
        user_id=seller.id,
        email_type="delivery_confirmation"
    )

def process_and_save_image(file: UploadFile, user_id: str) -> str:
    """Process and save uploaded image"""
    try:
        # Create unique filename
        file_extension = file.filename.split(".")[-1].lower()
        filename = f"profile_{user_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        filepath = UPLOAD_DIR / filename
        
        # Save original file
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process image (resize if too large)
        with Image.open(filepath) as img:
            # Convert to RGB if necessary
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            # Resize if too large (max 800x800)
            if img.width > 800 or img.height > 800:
                img.thumbnail((800, 800), Image.Resampling.LANCZOS)
                img.save(filepath, "JPEG", quality=85)
        
        return f"/static/{filename}"
    
    except Exception as e:
        # Clean up file if processing failed
        if filepath.exists():
            filepath.unlink()
        raise HTTPException(status_code=400, detail=f"Image processing failed: {str(e)}")

# File Upload Endpoint
@app.post("/api/upload/profile-image")
async def upload_profile_image(file: UploadFile = File(...)):
    """Upload and process profile image"""
    if not validate_image(file):
        raise HTTPException(
            status_code=400, 
            detail="Invalid file. Only JPEG, PNG, WebP files under 5MB are allowed."
        )
    
    # Generate temporary user ID for processing
    temp_user_id = str(uuid.uuid4())
    image_url = process_and_save_image(file, temp_user_id)
    
    return {
        "success": True,
        "image_url": image_url,
        "message": "Profil fotoğrafı başarıyla yüklendi!"
    }

# Auth endpoints
@app.post("/api/auth/register")
async def register_user(user_data: UserCreate):
    # Check if user already exists
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash password and create user
    hashed_password = get_password_hash(user_data.password)
    user = User(
        email=user_data.email,
        name=user_data.name,
        phone=user_data.phone,
        profile_image=user_data.profile_image,
        is_business_seller=user_data.is_business_seller,
        business_name=user_data.business_name,
        vat_number=user_data.vat_number
    )
    
    # Save to database
    user_dict = user.dict()
    user_dict["password"] = hashed_password
    user_dict["created_at"] = user_dict["created_at"].isoformat()
    
    try:
        logging.info(f"Attempting to save user: {user.id} with phone: {user.phone}")
        result = await db.users.insert_one(user_dict)
        logging.info(f"User saved with MongoDB ID: {result.inserted_id}")
        
        # Verify the user was saved
        saved_user = await db.users.find_one({"id": user.id})
        if saved_user:
            logging.info(f"User verification successful: {saved_user.get('phone')}")
        else:
            logging.error("User verification failed - user not found after insertion")
    except Exception as e:
        logging.error(f"Database insertion failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    # Send welcome email asynchronously
    try:
        asyncio.create_task(send_welcome_email(user, 'nl'))  # Default to Dutch
    except Exception as e:
        logging.error(f"Failed to send welcome email: {str(e)}")
    
    # Create access token
    access_token = create_access_token({"sub": user.id})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "phone": user.phone,
            "profile_image": user.profile_image
        }
    }

@app.post("/api/auth/login")
async def login_user(user_data: UserLogin):
    # Find user
    user_doc = await db.users.find_one({"email": user_data.email})
    if not user_doc or not verify_password(user_data.password, user_doc["password"]):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    # Create access token
    access_token = create_access_token({"sub": user_doc["id"]})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user_doc["id"],
            "email": user_doc["email"],
            "name": user_doc["name"],
            "profile_image": user_doc.get("profile_image")
        }
    }

# User profile endpoint
@app.get("/api/users/{user_id}")
async def get_user_profile(user_id: str):
    """Get user profile information"""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "id": user["id"],
        "name": user["name"],
        "profile_image": user["profile_image"],
        "is_verified": user.get("is_verified", False),
        "created_at": user["created_at"]
    }

# Location endpoints
@app.post("/api/geocode")
async def geocode_address_endpoint(address_data: Dict[str, str]):
    """Geocode an address to coordinates"""
    address = address_data.get('address')
    if not address:
        raise HTTPException(status_code=400, detail="Address is required")
    
    result = geocode_address(address)
    if not result:
        raise HTTPException(status_code=404, detail="Adres bulunamadı veya geçersiz")
    
    return {
        "success": True,
        "coordinates": {
            "lat": result['latitude'],
            "lng": result['longitude']
        },
        "formatted_address": result['formatted_address'],
        "components": result['components'],
        "place_id": result.get('place_id')
    }

@app.post("/api/reverse-geocode")
async def reverse_geocode_endpoint(coordinates: Dict[str, float]):
    """Convert coordinates to address"""
    lat = coordinates.get('lat')
    lng = coordinates.get('lng')
    
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="Latitude and longitude are required")
    
    address = reverse_geocode_coordinates(lat, lng)
    if not address:
        raise HTTPException(status_code=404, detail="Bu konum için adres bulunamadı")
    
    return {
        "success": True,
        "address": address
    }

@app.get("/api/nearby-places")
async def nearby_places_endpoint(
    lat: float,
    lng: float,
    radius: float = 5.0
):
    """Find nearby places for reference"""
    places = find_nearby_locations(lat, lng, radius)
    
    return {
        "success": True,
        "places": places,
        "count": len(places)
    }

# Product endpoints
# Image Upload Endpoint
@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    """Upload product image and return URL"""
    # Validate file type
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Invalid file type. Only JPEG, PNG, and WEBP are allowed.")
    
    # Validate file size (max 5MB)
    max_size = 5 * 1024 * 1024  # 5MB
    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 5MB.")
    
    # Generate unique filename
    file_extension = file.filename.split('.')[-1]
    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    
    # Create uploads directory if it doesn't exist
    upload_dir = "/app/uploads/products"
    os.makedirs(upload_dir, exist_ok=True)
    
    # Save file
    file_path = os.path.join(upload_dir, unique_filename)
    with open(file_path, "wb") as f:
        f.write(contents)
    
    # Return URL (relative path that frontend can use)
    file_url = f"/uploads/products/{unique_filename}"
    
    return {"url": file_url, "filename": unique_filename}

@app.post("/api/products", response_model=Product)
async def create_product(product_data: ProductCreate, current_user: User = Depends(get_current_user)):
    # Process location data
    location_data = None
    location_id = str(uuid.uuid4())
    
    # If coordinates are provided, use them; otherwise geocode the address
    if product_data.pickup_coordinates:
        lat = product_data.pickup_coordinates['lat']
        lng = product_data.pickup_coordinates['lng']
        
        # Reverse geocode to get formatted address
        formatted_address = reverse_geocode_coordinates(lat, lng)
        if formatted_address:
            location_data = {
                'id': location_id,
                'address': formatted_address,
                'latitude': lat,
                'longitude': lng,
                'country': 'Netherlands',
                'created_at': datetime.now(timezone.utc).isoformat()
            }
    else:
        # Geocode the provided address
        geocode_result = geocode_address(product_data.pickup_address)
        if geocode_result:
            location_data = {
                'id': location_id,
                'address': geocode_result['formatted_address'],
                'latitude': geocode_result['latitude'],
                'longitude': geocode_result['longitude'],
                'city': geocode_result['components'].get('city'),
                'postal_code': geocode_result['components'].get('postal_code'),
                'country': geocode_result['components'].get('country', 'Netherlands'),
                'created_at': datetime.now(timezone.utc).isoformat()
            }
    
    if not location_data:
        # Fallback: use provided address with default coordinates if geocoding fails
        location_data = {
            'id': location_id,
            'address': product_data.pickup_address,
            'latitude': product_data.pickup_coordinates['lat'] if product_data.pickup_coordinates else 52.3676,  # Default to Amsterdam
            'longitude': product_data.pickup_coordinates['lng'] if product_data.pickup_coordinates else 4.9041,
            'country': 'Netherlands',
            'created_at': datetime.now(timezone.utc).isoformat()
        }
    
    # Save location to database

# Helper function for optional authentication
async def get_current_user_optional(request: Request):
    """Get current user if authenticated, otherwise return None"""
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return None
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return None
        
        user = await db.users.find_one({"email": email})
        if not user:
            return None
        
        return User(**user)
    except:
        return None

# Product Recommendation Endpoints
@app.get("/api/products/{product_id}/similar")
async def get_similar_products(product_id: str, limit: int = 6):
    """Get similar products based on category and price"""
    # Get the reference product
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Define price range (±30%)
    price = product.get("price", 0)
    min_price = price * 0.7
    max_price = price * 1.3
    
    # Find similar products
    similar = await db.products.find({
        "id": {"$ne": product_id},
        "category": product.get("category"),
        "is_sold": False,
        "price": {"$gte": min_price, "$lte": max_price}
    }).limit(limit).to_list(None)
    
    # If not enough in same category, add from other categories
    if len(similar) < limit:
        additional = await db.products.find({
            "id": {"$ne": product_id},
            "category": {"$ne": product.get("category")},
            "is_sold": False,
            "price": {"$gte": min_price, "$lte": max_price}
        }).limit(limit - len(similar)).to_list(None)
        similar.extend(additional)
    
    # Remove MongoDB _id
    for p in similar:
        if "_id" in p:
            del p["_id"]
    
    return similar

@app.get("/api/products/recommended")
async def get_recommended_products(limit: int = 8, current_user: User = Depends(get_current_user_optional)):
    """Get personalized product recommendations"""
    recommendations = []
    
    if current_user:
        # Get user's wishlist
        wishlist = await db.wishlists.find({"user_id": current_user.id}).to_list(None)
        wishlist_product_ids = [w.get("product_id") for w in wishlist]
        
        # Get categories from wishlist
        wishlist_products = await db.products.find({
            "id": {"$in": wishlist_product_ids}
        }).to_list(None)
        
        favorite_categories = {}
        for p in wishlist_products:
            cat = p.get("category", "Other")
            favorite_categories[cat] = favorite_categories.get(cat, 0) + 1
        
        # Get products from favorite categories
        if favorite_categories:
            top_category = max(favorite_categories, key=favorite_categories.get)
            recommendations = await db.products.find({
                "category": top_category,
                "is_sold": False,
                "id": {"$nin": wishlist_product_ids}
            }).sort("views", -1).limit(limit).to_list(None)
    
    # If not enough recommendations, add trending products
    if len(recommendations) < limit:
        trending = await db.products.find({
            "is_sold": False
        }).sort("views", -1).limit(limit - len(recommendations)).to_list(None)
        recommendations.extend(trending)
    
    # Remove MongoDB _id
    for p in recommendations:
        if "_id" in p:
            del p["_id"]
    
    return recommendations

@app.get("/api/products/trending")
async def get_trending_products(limit: int = 10):
    """Get trending products based on views and wishlist"""
    # Get products with most views
    products = await db.products.find({
        "is_sold": False
    }).sort("views", -1).limit(limit * 2).to_list(None)
    
    # Enrich with wishlist count
    for product in products:
        wishlist_count = await db.wishlists.count_documents({"product_id": product["id"]})
        product["wishlist_count"] = wishlist_count
        product["trending_score"] = product.get("views", 0) + (wishlist_count * 5)
    
    # Sort by trending score
    products.sort(key=lambda x: x.get("trending_score", 0), reverse=True)
    
    # Remove MongoDB _id
    for p in products[:limit]:
        if "_id" in p:
            del p["_id"]
    
    return products[:limit]

@app.post("/api/products/{product_id}/view")
async def track_product_view(product_id: str):
    """Increment product view count"""
    await db.products.update_one(
        {"id": product_id},
        {"$inc": {"views": 1}}
    )
    return {"message": "View tracked"}

    await db.locations.insert_one(location_data)
    
    # Create product with location reference
    product = Product(
        title=product_data.title,
        description=product_data.description,
        price=product_data.price,
        category=product_data.category,
        condition=product_data.condition,
        images=product_data.images,
        location_id=location_id,
        pickup_location={
            'address': location_data['address'],
            'coordinates': {
                'lat': location_data['latitude'],
                'lng': location_data['longitude']
            }
        },
        seller_id=current_user.id,
        seller_name=current_user.name
    )
    
    product_dict = product.dict()
    product_dict["created_at"] = product_dict["created_at"].isoformat()
    
    await db.products.insert_one(product_dict)
    return product

# Enhanced Product Response with Seller Info
class ProductWithSeller(BaseModel):
    id: str
    title: str
    description: str
    price: float
    category: str
    condition: str
    images: List[str]
    location_id: str = Field(..., description="Reference to location")
    pickup_location: Optional[Dict[str, Any]] = None
    seller_id: str
    seller_name: str
    seller_profile_image: Optional[str] = None
    seller_rating: float = Field(default=0.0)
    seller_rating_count: int = Field(default=0)
    is_featured_seller: bool = Field(default=False)
    created_at: datetime
    is_sold: bool = False
    views: int = 0

@app.get("/api/products/featured")
async def get_featured_products(limit: int = 6):
    """Get featured products for homepage"""
    products = await db.products.find({
        "is_featured": True,
        "is_sold": False
    }).limit(limit).sort("created_at", -1).to_list(None)
    
    return products

@app.get("/api/products")
async def get_products(
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    location: Optional[str] = None,
    condition: Optional[str] = None,
    seller_type: Optional[str] = None,
    date_range: Optional[str] = None,
    sort_by: Optional[str] = "newest",
    limit: int = 20,
    skip: int = 0,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    distance: Optional[float] = None
):
    # Build filter query
    filter_query = {"is_sold": False}
    
    if category:
        filter_query["category"] = category
    if location:
        filter_query["pickup_address"] = {"$regex": location, "$options": "i"}
    
    # Condition filter
    if condition:
        filter_query["condition"] = condition
    
    # Seller type filter
    if seller_type:
        if seller_type == "business":
            # Find business sellers
            business_sellers = await db.users.find({"is_business_seller": True}).to_list(None)
            business_seller_ids = [s["id"] for s in business_sellers]
            filter_query["seller_id"] = {"$in": business_seller_ids}
        elif seller_type == "individual":
            # Find individual sellers
            individual_sellers = await db.users.find({"is_business_seller": {"$ne": True}}).to_list(None)
            individual_seller_ids = [s["id"] for s in individual_sellers]
            filter_query["seller_id"] = {"$in": individual_seller_ids}
    
    # Date range filter
    if date_range:
        now = datetime.now(timezone.utc)
        if date_range == "24h":
            filter_query["created_at"] = {"$gte": (now - timedelta(hours=24)).isoformat()}
        elif date_range == "7d":
            filter_query["created_at"] = {"$gte": (now - timedelta(days=7)).isoformat()}
        elif date_range == "30d":
            filter_query["created_at"] = {"$gte": (now - timedelta(days=30)).isoformat()}
    
    # Price range filter
    if min_price is not None or max_price is not None:
        price_filter = {}
        if min_price is not None:
            price_filter["$gte"] = min_price
        if max_price is not None:
            price_filter["$lte"] = max_price
        filter_query["price"] = price_filter
    
    # Search filter
    if search:
        filter_query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}}
        ]
    
    # Build sort criteria
    sort_criteria = []
    if sort_by == "newest":
        sort_criteria.append(("created_at", -1))
    elif sort_by == "oldest":
        sort_criteria.append(("created_at", 1))
    elif sort_by == "price_low":
        sort_criteria.append(("price", 1))
    elif sort_by == "price_high":
        sort_criteria.append(("price", -1))
    elif sort_by == "popular":
        sort_criteria.append(("views", -1))
    else:
        sort_criteria.append(("created_at", -1))  # default
    
    products = await db.products.find(filter_query).sort(sort_criteria).skip(skip).limit(limit).to_list(None)
    
    # Distance filtering (if location coordinates provided)
    if lat is not None and lng is not None and distance is not None:
        filtered_products = []
        for product in products:
            if product.get("pickup_location") and product["pickup_location"].get("coordinates"):
                prod_coords = product["pickup_location"]["coordinates"]
                # Calculate distance using Haversine formula (simplified)
                from math import radians, sin, cos, sqrt, atan2
                R = 6371  # Earth radius in km
                
                lat1, lon1 = radians(lat), radians(lng)
                lat2, lon2 = radians(prod_coords["lat"]), radians(prod_coords["lng"])
                
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                
                a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                c = 2 * atan2(sqrt(a), sqrt(1-a))
                dist = R * c
                
                if dist <= distance:
                    product["distance"] = round(dist, 2)
                    filtered_products.append(product)
        products = filtered_products
        
        # Sort by distance if requested
        if sort_by == "nearest":
            products.sort(key=lambda x: x.get("distance", 999999))
    
    # Enrich products with seller information
    enriched_products = []
    for product in products:
        # Remove MongoDB _id field to avoid serialization issues
        if "_id" in product:
            del product["_id"]
            
        # Get seller info
        seller = await db.users.find_one({"id": product["seller_id"]})
        
        # Remove seller_name from product if it exists (will be added from seller info)
        product_copy = {k: v for k, v in product.items() if k not in ["seller_name", "seller_rating", "seller_rating_count", "is_featured_seller", "seller_profile_image"]}
        
        if seller:
            # Add seller info
            product_copy["seller_name"] = seller.get("name", "Unknown")
            product_copy["seller_rating"] = seller.get("rating_average", 0.0)
            product_copy["seller_rating_count"] = seller.get("rating_count", 0)
            product_copy["is_featured_seller"] = seller.get("is_featured_seller", False)
            product_copy["seller_profile_image"] = seller.get("profile_image")
        else:
            # Fallback if seller not found
            product_copy["seller_name"] = "Unknown"
        
        enriched_products.append(ProductWithSeller(**product_copy))
    
    # Sort to show featured sellers first (only if no specific sort requested)
    if sort_by == "featured":
        enriched_products.sort(key=lambda x: (x.is_featured_seller, x.seller_rating), reverse=True)
    
    return enriched_products

@app.get("/api/products/{product_id}", response_model=Product)
async def get_product(product_id: str):
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Increment views
    await db.products.update_one(
        {"id": product_id},
        {"$inc": {"views": 1}}
    )
    
    return Product(**product)

# Rating endpoints
@app.post("/api/ratings", response_model=Rating)
async def create_rating(rating_data: RatingCreate, current_user: User = Depends(get_current_user)):
    """Create a new rating for a seller"""
    
    # Verify transaction exists and current user was the buyer
    transaction = await db.transactions.find_one({
        "id": rating_data.transaction_id,
        "buyer_id": current_user.id,
        "status": "completed"
    })
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found or not eligible for rating")
    
    # Check if rating already exists for this transaction
    existing_rating = await db.ratings.find_one({
        "transaction_id": rating_data.transaction_id,
        "rater_id": current_user.id
    })
    
    if existing_rating:
        raise HTTPException(status_code=400, detail="Rating already exists for this transaction")
    
    # Create rating
    rating = Rating(
        rater_id=current_user.id,
        rated_user_id=rating_data.rated_user_id,
        transaction_id=rating_data.transaction_id,
        rating=rating_data.rating,
        comment=rating_data.comment
    )
    
    rating_dict = rating.dict()
    rating_dict["created_at"] = rating_dict["created_at"].isoformat()
    
    await db.ratings.insert_one(rating_dict)
    
    # Update seller's rating average
    await update_seller_rating(rating_data.rated_user_id)
    
    return rating

async def update_seller_rating(seller_id: str):
    """Update seller's average rating and count"""
    # Calculate new average rating
    pipeline = [
        {"$match": {"rated_user_id": seller_id}},
        {"$group": {
            "_id": None,
            "avg_rating": {"$avg": "$rating"},
            "count": {"$sum": 1}
        }}
    ]
    
    result = await db.ratings.aggregate(pipeline).to_list(None)
    
    if result:
        avg_rating = round(result[0]["avg_rating"], 1)
        count = result[0]["count"]
        
        # Update user record
        await db.users.update_one(
            {"id": seller_id},
            {
                "$set": {
                    "rating_average": avg_rating,
                    "rating_count": count,
                    "is_featured_seller": avg_rating >= 4.5 and count >= 5  # Auto-feature sellers with high ratings
                }
            }
        )

@app.get("/api/users/{user_id}/ratings")
async def get_user_ratings(user_id: str, limit: int = 10, skip: int = 0):
    """Get ratings for a specific seller"""
    ratings_cursor = db.ratings.find({"rated_user_id": user_id}).sort("created_at", -1).skip(skip).limit(limit)
    ratings = await ratings_cursor.to_list(None)
    
    # Get rater info for each rating
    enriched_ratings = []
    for rating in ratings:
        rater = await db.users.find_one({"id": rating["rater_id"]})
        rating_with_rater = {
            **rating,
            "rater_name": rater["name"] if rater else "Anonymous",
            "rater_profile_image": rater.get("profile_image") if rater else None
        }
        enriched_ratings.append(rating_with_rater)
    
    return enriched_ratings

@app.get("/api/featured-sellers")
async def get_featured_sellers(limit: int = 8):
    """Get featured sellers (high-rated sellers)"""
    sellers = await db.users.find({
        "is_featured_seller": True,
        "rating_count": {"$gte": 5}
    }).sort("rating_average", -1).limit(limit).to_list(None)
    
    return [User(**seller) for seller in sellers]

# Cart endpoints
@app.get("/api/cart")
async def get_cart(current_user: User = Depends(get_current_user)):
    """Get user's current cart"""
    cart = await db.carts.find_one({"user_id": current_user.id})
    
    if not cart:
        # Create empty cart
        new_cart = Cart(user_id=current_user.id)
        cart_dict = new_cart.dict()
        cart_dict["created_at"] = cart_dict["created_at"].isoformat()
        cart_dict["updated_at"] = cart_dict["updated_at"].isoformat()
        
        await db.carts.insert_one(cart_dict)
        return new_cart
    
    return Cart(**cart)

@app.post("/api/cart/add")
async def add_to_cart(product_id: str, current_user: User = Depends(get_current_user)):
    """Add product to cart"""
    # Check if product exists and is available
    product = await db.products.find_one({"id": product_id, "is_sold": False})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found or already sold")
    
    # Get or create cart
    cart = await db.carts.find_one({"user_id": current_user.id})
    
    if not cart:
        # Create new cart with item
        new_cart = Cart(
            user_id=current_user.id,
            items=[CartItem(product_id=product_id)]
        )
        cart_dict = new_cart.dict()
        cart_dict["created_at"] = cart_dict["created_at"].isoformat()
        cart_dict["updated_at"] = cart_dict["updated_at"].isoformat()
        cart_dict["items"] = [item.dict() for item in cart_dict["items"]]
        for item in cart_dict["items"]:
            item["added_at"] = item["added_at"].isoformat()
        
        await db.carts.insert_one(cart_dict)
        
        return {"success": True, "message": "Product added to cart"}
    else:
        # Check if item already in cart
        existing_items = cart.get("items", [])
        if any(item.get("product_id") == product_id for item in existing_items):
            raise HTTPException(status_code=400, detail="Product already in cart")
        
        # Add new item to existing cart
        new_item = CartItem(product_id=product_id)
        new_item_dict = new_item.dict()
        new_item_dict["added_at"] = new_item_dict["added_at"].isoformat()
        
        await db.carts.update_one(
            {"user_id": current_user.id},
            {
                "$push": {"items": new_item_dict},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
            }
        )
        
        return {"success": True, "message": "Product added to cart"}

@app.delete("/api/cart/remove/{product_id}")
async def remove_from_cart(product_id: str, current_user: User = Depends(get_current_user)):
    """Remove product from cart"""
    result = await db.carts.update_one(
        {"user_id": current_user.id},
        {
            "$pull": {"items": {"product_id": product_id}},
            "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Product not found in cart")
    
    return {"success": True, "message": "Product removed from cart"}

# Background task for abandoned cart emails
@app.post("/api/send-order-emails/{transaction_id}")
async def send_order_emails(transaction_id: str):
    """Send order confirmation emails to buyer and seller"""
    transaction = await db.transactions.find_one({"id": transaction_id})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Get buyer, seller, and product info
    buyer = await db.users.find_one({"id": transaction["buyer_id"]})
    seller = await db.users.find_one({"id": transaction["seller_id"]})
    product = await db.products.find_one({"id": transaction["product_id"]})
    
    if not buyer or not seller or not product:
        raise HTTPException(status_code=404, detail="Related data not found")
    
    try:
        # Send order confirmation to buyer
        asyncio.create_task(send_order_confirmation_email(
            User(**buyer),
            product["title"],
            transaction_id,
            transaction["total_amount"],
            'nl'  # Default to Dutch
        ))
        
        # Send sale notification to seller
        asyncio.create_task(send_seller_notification_email(
            User(**seller),
            buyer["name"],
            product["title"],
            transaction_id,
            transaction["amount"],  # Product price without commission
            'nl'
        ))
        
        return {"success": True, "message": "Order emails sent"}
    except Exception as e:
        logging.error(f"Failed to send order emails: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to send emails")

@app.post("/api/send-cart-reminders")
async def send_cart_reminders():
    """Background task to send abandoned cart reminder emails"""
    # Find carts that haven't been updated in 24 hours and haven't sent reminder yet
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
    
    abandoned_carts = await db.carts.find({
        "updated_at": {"$lt": cutoff_time.isoformat()},
        "reminder_sent": False,
        "items": {"$exists": True, "$ne": []}
    }).to_list(None)
    
    reminder_count = 0
    
    for cart in abandoned_carts:
        try:
            # Get user info
            user = await db.users.find_one({"id": cart["user_id"]})
            if not user:
                continue
            
            # Get product titles for cart items
            product_titles = []
            for item in cart["items"]:
                product = await db.products.find_one({"id": item["product_id"]})
                if product and not product.get("is_sold", False):
                    product_titles.append(product["title"])
            
            if not product_titles:
                continue
            
            # Send reminder email
            user_obj = User(**user)
            success = await send_cart_reminder_email(user_obj, product_titles, 'nl')
            
            if success:
                # Mark reminder as sent
                await db.carts.update_one(
                    {"id": cart["id"]},
                    {"$set": {"reminder_sent": True}}
                )
                reminder_count += 1
                
        except Exception as e:
            logging.error(f"Failed to send cart reminder to user {cart['user_id']}: {str(e)}")
    
    return {"reminders_sent": reminder_count}

# Categories endpoint
@app.get("/api/categories")
async def get_categories():
    return [
        "Electronics",
        "Fashion & Clothing",
        "Home & Garden",
        "Sports & Recreation",
        "Books & Media",
        "Toys & Games",
        "Cars & Vehicles",
        "Art & Antiques",
        "Musical Instruments",
        "Other"
    ]

# Support Ticket Endpoints
@app.post("/api/support/tickets", response_model=Ticket)
async def create_support_ticket(ticket_data: TicketCreate, current_user: User = Depends(get_current_user)):
    """Create a new support ticket"""
    ticket = Ticket(
        user_id=current_user.id,
        subject=ticket_data.subject,
        message=ticket_data.message,
        category=ticket_data.category,
        priority=ticket_data.priority
    )
    
    ticket_dict = ticket.dict()
    ticket_dict["created_at"] = ticket_dict["created_at"].isoformat()
    ticket_dict["updated_at"] = ticket_dict["updated_at"].isoformat()
    
    await db.support_tickets.insert_one(ticket_dict)
    
    # Send notification email to admin (optional)
    try:
        # TODO: Implement admin notification email
        logging.info(f"New support ticket created: {ticket.id} by user {current_user.id}")
    except Exception as e:
        logging.error(f"Failed to send admin notification: {str(e)}")
    
    return ticket

@app.get("/api/support/tickets")
async def get_user_tickets(current_user: User = Depends(get_current_user)):
    """Get all tickets for current user"""
    tickets = await db.support_tickets.find({
        "user_id": current_user.id
    }).sort("created_at", -1).to_list(None)
    
    return tickets

@app.get("/api/support/tickets/{ticket_id}")
async def get_ticket_details(ticket_id: str, current_user: User = Depends(get_current_user)):
    """Get ticket details with replies"""
    ticket = await db.support_tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Verify user owns this ticket
    if ticket["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this ticket")
    
    # Get replies
    replies = await db.support_ticket_replies.find({
        "ticket_id": ticket_id
    }).sort("created_at", 1).to_list(None)
    
    return {
        "ticket": ticket,
        "replies": replies
    }

@app.post("/api/support/tickets/{ticket_id}/reply")
async def reply_to_ticket(
    ticket_id: str,
    reply_data: TicketReplyCreate,
    current_user: User = Depends(get_current_user)
):
    """Add a reply to a support ticket"""
    ticket = await db.support_tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Verify user owns this ticket
    if ticket["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to reply to this ticket")
    
    # Create reply
    reply = TicketReply(
        ticket_id=ticket_id,
        user_id=current_user.id,
        message=reply_data.message,
        is_staff=False
    )
    
    reply_dict = reply.dict()
    reply_dict["created_at"] = reply_dict["created_at"].isoformat()
    
    await db.support_ticket_replies.insert_one(reply_dict)
    
    # Update ticket's updated_at
    await db.support_tickets.update_one(
        {"id": ticket_id},
        {"$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    return {"success": True, "reply_id": reply.id}

@app.put("/api/support/tickets/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: str,
    status_update: TicketStatusUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update ticket status (user can close their own tickets)"""
    ticket = await db.support_tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Verify user owns this ticket
    if ticket["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this ticket")
    
    # Users can only close their own tickets
    valid_statuses = ["closed"]
    if status_update.status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid status. Users can only close tickets.")
    
    await db.support_tickets.update_one(
        {"id": ticket_id},
        {
            "$set": {
                "status": status_update.status,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
        }
    )
    
    return {"success": True, "message": "Ticket status updated"}

# Notification Helper Function
async def create_notification(user_id: str, notification_type: str, title: str, message: str, link: str = None):
    """Helper function to create a notification"""
    try:
        notification = Notification(
            user_id=user_id,
            type=notification_type,
            title=title,
            message=message,
            link=link
        )
        
        notification_dict = notification.dict()
        notification_dict["created_at"] = notification_dict["created_at"].isoformat()
        
        await db.notifications.insert_one(notification_dict)
        logging.info(f"Notification created for user {user_id}: {title}")
    except Exception as e:
        logging.error(f"Failed to create notification: {str(e)}")

# Admin Endpoints
@app.get("/api/admin/stats")
async def get_admin_stats(admin: User = Depends(get_admin_user)):
    """Get admin dashboard statistics"""
    # Total counts
    total_users = await db.users.count_documents({})
    total_products = await db.products.count_documents({})
    total_transactions = await db.transactions.count_documents({})
    total_tickets = await db.support_tickets.count_documents({})
    
    # Revenue calculation
    completed_transactions = await db.transactions.find({"status": "completed"}).to_list(None)
    total_revenue = sum(t.get("commission", 0) for t in completed_transactions)
    
    # Pending orders
    pending_orders = await db.transactions.count_documents({"status": "pending"})
    
    # Today's stats
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_users = await db.users.count_documents({"created_at": {"$gte": today.isoformat()}})
    today_products = await db.products.count_documents({"created_at": {"$gte": today.isoformat()}})
    today_transactions = await db.transactions.count_documents({"created_at": {"$gte": today.isoformat()}})
    
    # Open tickets
    open_tickets = await db.support_tickets.count_documents({"status": "open"})
    
    return {
        "total_users": total_users,
        "total_products": total_products,
        "total_transactions": total_transactions,
        "total_tickets": total_tickets,
        "total_revenue": round(total_revenue, 2),
        "pending_orders": pending_orders,
        "open_tickets": open_tickets,
        "today": {
            "users": today_users,
            "products": today_products,
            "transactions": today_transactions
        }
    }

@app.get("/api/admin/users")
async def get_all_users(
    skip: int = 0,
    limit: int = 50,
    search: str = None,
    admin: User = Depends(get_admin_user)
):
    """Get all users with pagination"""
    query = {}
    if search:
        query = {"$or": [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]}
    
    users = await db.users.find(query).skip(skip).limit(limit).sort("created_at", -1).to_list(None)
    total = await db.users.count_documents(query)
    
    # Remove MongoDB _id field and password from users
    for user in users:
        if "_id" in user:
            del user["_id"]
        if "password" in user:
            del user["password"]
    
    return {
        "users": users,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@app.put("/api/admin/users/{user_id}/ban")
async def ban_user(user_id: str, admin: User = Depends(get_admin_user)):
    """Ban a user"""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.get("is_admin"):
        raise HTTPException(status_code=400, detail="Cannot ban admin users")
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"is_banned": True}}
    )
    
    return {"success": True, "message": "User banned successfully"}

@app.put("/api/admin/users/{user_id}/unban")
async def unban_user(user_id: str, admin: User = Depends(get_admin_user)):
    """Unban a user"""
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"is_banned": False}}
    )
    
    return {"success": True, "message": "User unbanned successfully"}

@app.put("/api/admin/users/{user_id}/verify")
async def verify_user(user_id: str, admin: User = Depends(get_admin_user)):
    """Verify a user"""
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"is_verified": True}}
    )
    
    return {"success": True, "message": "User verified successfully"}

@app.get("/api/admin/products")
async def get_all_products_admin(
    skip: int = 0,
    limit: int = 50,
    status: str = None,
    admin: User = Depends(get_admin_user)
):
    """Get all products for admin"""
    query = {}
    if status:
        query["is_sold"] = status == "sold"
    
    products = await db.products.find(query).skip(skip).limit(limit).sort("created_at", -1).to_list(None)
    total = await db.products.count_documents(query)
    
    # Enrich with seller info
    enriched_products = []
    for product in products:
        # Remove MongoDB _id field
        if "_id" in product:
            del product["_id"]
            
        seller = await db.users.find_one({"id": product["seller_id"]})
        enriched_products.append({
            **product,
            "seller_name": seller["name"] if seller else "Unknown",
            "seller_email": seller["email"] if seller else "Unknown"
        })
    
    return {
        "products": enriched_products,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@app.delete("/api/admin/products/{product_id}")
async def delete_product_admin(product_id: str, admin: User = Depends(get_admin_user)):
    """Delete a product (admin only)"""
    result = await db.products.delete_one({"id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return {"success": True, "message": "Product deleted successfully"}

@app.put("/api/admin/products/{product_id}/feature")
async def toggle_featured_product(product_id: str, featured: bool, admin: User = Depends(get_admin_user)):
    """Toggle featured status for a product"""
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    await db.products.update_one(
        {"id": product_id},
        {"$set": {"is_featured": featured}}
    )
    
    return {"success": True, "message": f"Product {'featured' if featured else 'unfeatured'} successfully"}

@app.get("/api/admin/tickets")
async def get_all_tickets_admin(
    skip: int = 0,
    limit: int = 50,
    status: str = None,
    admin: User = Depends(get_admin_user)
):
    """Get all support tickets for admin"""
    query = {}
    if status:
        query["status"] = status
    
    tickets = await db.support_tickets.find(query).skip(skip).limit(limit).sort("created_at", -1).to_list(None)
    total = await db.support_tickets.count_documents(query)
    
    # Enrich with user info
    enriched_tickets = []
    for ticket in tickets:
        user = await db.users.find_one({"id": ticket["user_id"]})
        enriched_tickets.append({
            **ticket,
            "user_name": user["name"] if user else "Unknown",
            "user_email": user["email"] if user else "Unknown"
        })
    
    return {
        "tickets": enriched_tickets,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@app.put("/api/admin/tickets/{ticket_id}/status")
async def update_ticket_status_admin(
    ticket_id: str,
    status: str,
    admin: User = Depends(get_admin_user)
):
    """Update ticket status (admin)"""
    valid_statuses = ["open", "in_progress", "resolved", "closed"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    await db.support_tickets.update_one(
        {"id": ticket_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    

# Report Endpoints
@app.post("/api/reports")
async def create_report(report_data: ReportCreate, current_user: User = Depends(get_current_user)):
    """Create a new report"""
    # Check if user already reported this entity
    existing = await db.reports.find_one({
        "reporter_id": current_user.id,
        "reported_type": report_data.reported_type,
        "reported_id": report_data.reported_id
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="You have already reported this")
    
    report = Report(
        reporter_id=current_user.id,
        reported_type=report_data.reported_type,
        reported_id=report_data.reported_id,
        reason=report_data.reason,
        description=report_data.description
    )
    
    report_dict = report.dict()
    report_dict["created_at"] = report_dict["created_at"].isoformat()
    
    await db.reports.insert_one(report_dict)
    
    # Increment reports_received for the reported user (if user report)
    if report_data.reported_type == "user":
        await db.users.update_one(
            {"id": report_data.reported_id},
            {"$inc": {"reports_received": 1}}
        )
        # Recalculate trust score
        await calculate_trust_score(report_data.reported_id)
    
    # Notify admins
    admins = await db.users.find({"is_admin": True}).to_list(None)
    for admin in admins:
        await create_notification(
            user_id=admin["id"],
            notification_type="system",
            title="New Report",
            message=f"New {report_data.reported_type} report: {report_data.reason}",
            link="/admin/reports"
        )
    
    return {"message": "Report submitted successfully", "report_id": report.id}

@app.get("/api/admin/reports")
async def get_all_reports(
    skip: int = 0,
    limit: int = 50,
    status: str = None,
    admin: User = Depends(get_admin_user)
):
    """Get all reports for admin review"""
    query = {}
    if status:
        query["status"] = status
    
    reports = await db.reports.find(query).skip(skip).limit(limit).sort("created_at", -1).to_list(None)
    total = await db.reports.count_documents(query)
    
    # Enrich with reporter and reported entity info
    enriched_reports = []
    for report in reports:
        if "_id" in report:
            del report["_id"]
        
        # Get reporter info
        reporter = await db.users.find_one({"id": report["reporter_id"]})
        report["reporter_name"] = reporter.get("name") if reporter else "Unknown"
        
        # Get reported entity info
        if report["reported_type"] == "user":
            reported = await db.users.find_one({"id": report["reported_id"]})
            report["reported_name"] = reported.get("name") if reported else "Unknown"
        elif report["reported_type"] == "product":
            product = await db.products.find_one({"id": report["reported_id"]})
            report["reported_name"] = product.get("title") if product else "Unknown"
        else:
            report["reported_name"] = "Message"
        
        enriched_reports.append(report)
    
    return {
        "reports": enriched_reports,
        "total": total,
        "page": skip // limit + 1,
        "pages": (total + limit - 1) // limit
    }

@app.put("/api/admin/reports/{report_id}/resolve")
async def resolve_report(
    report_id: str,
    action: str,  # dismiss, warn, ban
    notes: str = "",
    admin: User = Depends(get_admin_user)
):
    """Resolve a report"""
    report = await db.reports.find_one({"id": report_id})
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    # Update report status
    await db.reports.update_one(
        {"id": report_id},
        {"$set": {
            "status": "resolved",
            "admin_notes": notes,
            "resolved_by": admin.id,
            "resolved_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    # Take action based on admin decision
    if action == "ban" and report["reported_type"] == "user":
        await db.users.update_one(
            {"id": report["reported_id"]},
            {"$set": {"is_banned": True}}
        )
        # Notify banned user
        await create_notification(
            user_id=report["reported_id"],
            notification_type="system",
            title="Account Suspended",
            message="Your account has been suspended due to policy violations.",
            link="/dashboard"
        )
    elif action == "warn" and report["reported_type"] == "user":
        # Send warning notification
        await create_notification(
            user_id=report["reported_id"],
            notification_type="system",
            title="Warning",
            message="You have received a warning. Please review our community guidelines.",
            link="/faq"
        )
    
    return {"message": "Report resolved successfully"}

# Verification Endpoints
@app.post("/api/verification/request")
async def request_verification(current_user: User = Depends(get_current_user)):
    """Request account verification"""
    # Check if already verified
    if current_user.is_verified:
        raise HTTPException(status_code=400, detail="Account already verified")
    
    # Check if request already pending
    existing = await db.verification_requests.find_one({
        "user_id": current_user.id,
        "status": "pending"
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="Verification request already pending")
    
    # Create verification request
    request = VerificationRequest(
        user_id=current_user.id,
        email_verified=True  # Assume email is verified at registration
    )
    
    request_dict = request.dict()
    request_dict["created_at"] = request_dict["created_at"].isoformat()
    
    await db.verification_requests.insert_one(request_dict)
    
    # Update user
    await db.users.update_one(
        {"id": current_user.id},
        {"$set": {
            "verification_requested": True,
            "verification_requested_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    # Notify admins
    admins = await db.users.find({"is_admin": True}).to_list(None)
    for admin in admins:
        await create_notification(
            user_id=admin["id"],
            notification_type="system",
            title="New Verification Request",
            message=f"{current_user.name} requested account verification",
            link="/admin/verifications"
        )
    
    return {"message": "Verification request submitted successfully"}

@app.get("/api/admin/verifications")
async def get_verification_requests(
    skip: int = 0,
    limit: int = 50,
    status: str = None,
    admin: User = Depends(get_admin_user)
):
    """Get all verification requests"""
    query = {}
    if status:
        query["status"] = status
    
    requests = await db.verification_requests.find(query).skip(skip).limit(limit).sort("created_at", -1).to_list(None)
    total = await db.verification_requests.count_documents(query)
    
    # Enrich with user info
    enriched_requests = []
    for req in requests:
        if "_id" in req:
            del req["_id"]
        
        user = await db.users.find_one({"id": req["user_id"]})
        if user:
            req["user_name"] = user.get("name")
            req["user_email"] = user.get("email")
            req["user_trust_score"] = user.get("trust_score", 50)
            req["user_completed_transactions"] = user.get("completed_transactions", 0)
        
        enriched_requests.append(req)
    
    return {
        "requests": enriched_requests,
        "total": total,
        "page": skip // limit + 1,
        "pages": (total + limit - 1) // limit
    }

# Seller Dashboard Endpoints
@app.get("/api/seller/dashboard")
async def get_seller_dashboard(current_user: User = Depends(get_current_user)):
    """Get seller dashboard overview"""
    # Get seller's products
    products = await db.products.find({"seller_id": current_user.id}).to_list(None)
    total_products = len(products)
    active_products = len([p for p in products if not p.get("is_sold", False)])
    sold_products = total_products - active_products
    
    # Get total views
    total_views = sum(p.get("views", 0) for p in products)
    
    # Get transactions (completed sales)
    transactions = await db.transactions.find({
        "seller_id": current_user.id,
        "status": "completed"
    }).to_list(None)
    
    total_sales = len(transactions)
    total_revenue = sum(t.get("product_price", 0) for t in transactions)
    total_commission = sum(t.get("commission", 0) for t in transactions)
    net_revenue = total_revenue - total_commission
    
    # Get average rating
    reviews = await db.reviews.find({"seller_id": current_user.id}).to_list(None)
    avg_rating = sum(r.get("rating", 0) for r in reviews) / len(reviews) if reviews else 0
    
    # Get messages count
    conversations = await db.conversations.find({
        "$or": [
            {"seller_id": current_user.id},
            {"buyer_id": current_user.id}
        ]
    }).to_list(None)
    
    # Recent activity (last 30 days)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_sales = len([t for t in transactions if datetime.fromisoformat(t.get("created_at", "")) > thirty_days_ago])
    recent_views = sum(p.get("views", 0) for p in products if datetime.fromisoformat(p.get("created_at", "")) > thirty_days_ago)
    
    return {
        "total_products": total_products,
        "active_products": active_products,
        "sold_products": sold_products,
        "total_views": total_views,
        "total_sales": total_sales,
        "total_revenue": round(total_revenue, 2),
        "total_commission": round(total_commission, 2),
        "net_revenue": round(net_revenue, 2),
        "average_rating": round(avg_rating, 2),
        "total_reviews": len(reviews),
        "active_conversations": len(conversations),
        "recent_sales_30d": recent_sales,
        "recent_views_30d": recent_views
    }

@app.get("/api/seller/products/performance")
async def get_seller_products_performance(
    limit: int = 10,
    current_user: User = Depends(get_current_user)
):
    """Get seller's product performance metrics"""
    products = await db.products.find({"seller_id": current_user.id}).to_list(None)
    
    # Enrich with sales data
    performance_data = []
    for product in products:
        # Get sales count
        sales_count = await db.transactions.count_documents({
            "product_id": product["id"],
            "status": "completed"
        })
        
        # Get revenue
        transactions = await db.transactions.find({
            "product_id": product["id"],
            "status": "completed"
        }).to_list(None)
        
        revenue = sum(t.get("product_price", 0) for t in transactions)
        
        # Get reviews
        reviews = await db.reviews.find({"product_id": product["id"]}).to_list(None)
        avg_rating = sum(r.get("rating", 0) for r in reviews) / len(reviews) if reviews else 0
        
        # Get wishlist count
        wishlist_count = await db.wishlists.count_documents({"product_id": product["id"]})
        
        performance_data.append({
            "product_id": product["id"],
            "title": product["title"],
            "price": product["price"],
            "views": product.get("views", 0),
            "sales_count": sales_count,
            "revenue": round(revenue, 2),
            "average_rating": round(avg_rating, 2),
            "review_count": len(reviews),
            "wishlist_count": wishlist_count,
            "is_sold": product.get("is_sold", False),
            "created_at": product.get("created_at"),
            "category": product.get("category")
        })
    
    # Sort by views (most popular)
    performance_data.sort(key=lambda x: x["views"], reverse=True)
    
    return performance_data[:limit]

@app.get("/api/seller/sales-trend")
async def get_seller_sales_trend(
    days: int = 30,
    current_user: User = Depends(get_current_user)
):
    """Get seller's sales trend over time"""
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    # Get all transactions in range
    transactions = await db.transactions.find({
        "seller_id": current_user.id,
        "status": "completed",
        "created_at": {"$gte": start_date.isoformat()}
    }).to_list(None)
    
    # Group by date
    daily_sales = {}
    for t in transactions:
        try:
            date_str = t.get("created_at", "")[:10]
            if date_str not in daily_sales:
                daily_sales[date_str] = {
                    "sales_count": 0,
                    "revenue": 0,
                    "commission": 0
                }
            daily_sales[date_str]["sales_count"] += 1
            daily_sales[date_str]["revenue"] += t.get("product_price", 0)
            daily_sales[date_str]["commission"] += t.get("commission", 0)
        except:
            continue
    
    # Convert to list
    trend_data = []
    for date_str in sorted(daily_sales.keys()):
        trend_data.append({
            "date": date_str,
            "sales": daily_sales[date_str]["sales_count"],
            "revenue": round(daily_sales[date_str]["revenue"], 2),
            "commission": round(daily_sales[date_str]["commission"], 2),
            "net": round(daily_sales[date_str]["revenue"] - daily_sales[date_str]["commission"], 2)
        })
    
    return trend_data

@app.get("/api/seller/revenue")
async def get_seller_revenue(current_user: User = Depends(get_current_user)):
    """Get detailed revenue breakdown"""
    transactions = await db.transactions.find({
        "seller_id": current_user.id,
        "status": "completed"
    }).to_list(None)
    
    # Total revenue
    total_revenue = sum(t.get("product_price", 0) for t in transactions)
    total_commission = sum(t.get("commission", 0) for t in transactions)
    net_revenue = total_revenue - total_commission
    
    # By category
    category_revenue = {}
    for t in transactions:
        product = await db.products.find_one({"id": t.get("product_id")})
        if product:
            category = product.get("category", "Other")
            if category not in category_revenue:
                category_revenue[category] = 0
            category_revenue[category] += t.get("product_price", 0)
    
    category_breakdown = [
        {"category": cat, "revenue": round(rev, 2)}
        for cat, rev in sorted(category_revenue.items(), key=lambda x: x[1], reverse=True)
    ]
    
    # By month (last 6 months)
    monthly_revenue = {}
    for t in transactions:
        try:
            month_str = t.get("created_at", "")[:7]  # YYYY-MM
            if month_str not in monthly_revenue:
                monthly_revenue[month_str] = 0
            monthly_revenue[month_str] += t.get("product_price", 0)
        except:
            continue
    
    monthly_breakdown = [
        {"month": month, "revenue": round(rev, 2)}
        for month, rev in sorted(monthly_revenue.items())[-6:]
    ]
    
    return {
        "total_revenue": round(total_revenue, 2),
        "total_commission": round(total_commission, 2),
        "net_revenue": round(net_revenue, 2),
        "average_order_value": round(total_revenue / len(transactions), 2) if transactions else 0,
        "category_breakdown": category_breakdown,
        "monthly_breakdown": monthly_breakdown
    }

    """Get all verification requests"""
    query = {}
    if status:
        query["status"] = status
    
    requests = await db.verification_requests.find(query).skip(skip).limit(limit).sort("created_at", -1).to_list(None)
    total = await db.verification_requests.count_documents(query)
    
    # Enrich with user info
    enriched_requests = []
    for req in requests:
        if "_id" in req:
            del req["_id"]
        
        user = await db.users.find_one({"id": req["user_id"]})
        if user:
            req["user_name"] = user.get("name")
            req["user_email"] = user.get("email")
            req["user_trust_score"] = user.get("trust_score", 50)
            req["user_completed_transactions"] = user.get("completed_transactions", 0)
        
        enriched_requests.append(req)
    
    return {
        "requests": enriched_requests,
        "total": total,
        "page": skip // limit + 1,
        "pages": (total + limit - 1) // limit
    }

@app.put("/api/admin/verifications/{request_id}/approve")
async def approve_verification(
    request_id: str,
    approved: bool,
    notes: str = "",
    admin: User = Depends(get_admin_user)
):
    """Approve or reject verification request"""
    request = await db.verification_requests.find_one({"id": request_id})
    if not request:
        raise HTTPException(status_code=404, detail="Verification request not found")
    
    # Update request
    await db.verification_requests.update_one(
        {"id": request_id},
        {"$set": {
            "status": "approved" if approved else "rejected",
            "admin_notes": notes,
            "reviewed_by": admin.id,
            "reviewed_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    # Update user if approved
    if approved:
        await db.users.update_one(
            {"id": request["user_id"]},
            {"$set": {
                "is_verified": True,
                "verification_requested": False
            }}
        )
        # Recalculate trust score (verification adds +10)
        await calculate_trust_score(request["user_id"])
        
        # Notify user
        await create_notification(
            user_id=request["user_id"],
            notification_type="system",
            title="Verification Approved ✓",
            message="Congratulations! Your account has been verified.",
            link="/dashboard"
        )
    else:
        await db.users.update_one(
            {"id": request["user_id"]},
            {"$set": {"verification_requested": False}}
        )
        # Notify user
        await create_notification(
            user_id=request["user_id"],
            notification_type="system",
            title="Verification Request",
            message=f"Your verification request was not approved. {notes}",
            link="/dashboard"
        )
    
    return {"message": "Verification request processed successfully"}

@app.get("/api/users/{user_id}/trust-score")
async def get_user_trust_score(user_id: str):
    """Get user's trust score (public endpoint)"""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Recalculate if needed (cache for 24 hours)
    score = user.get("trust_score", 50)
    
    return {
        "trust_score": score,
        "badge": get_trust_badge(score),
        "is_verified": user.get("is_verified", False),
        "completed_transactions": user.get("completed_transactions", 0),
        "rating_average": user.get("rating_average", 0),
        "rating_count": user.get("rating_count", 0)
    }

    return {"success": True, "message": "Ticket status updated"}

@app.post("/api/admin/tickets/{ticket_id}/reply")
async def admin_reply_ticket(
    ticket_id: str,
    message: str,
    admin: User = Depends(get_admin_user)
):
    """Admin reply to ticket"""
    ticket = await db.support_tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    reply = TicketReply(
        ticket_id=ticket_id,
        user_id=admin.id,
        message=message,
        is_staff=True  # Mark as staff reply
    )
    
    reply_dict = reply.dict()
    reply_dict["created_at"] = reply_dict["created_at"].isoformat()
    
    await db.support_ticket_replies.insert_one(reply_dict)
    
    # Update ticket
    await db.support_tickets.update_one(
        {"id": ticket_id},
        {"$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    # Send notification to user
    asyncio.create_task(create_notification(
        user_id=ticket["user_id"],
        notification_type="support",
        title="Support Team Replied",
        message=f"You have a new response to your ticket: {ticket['subject']}",
        link="/support"
    ))
    
    return {"success": True, "reply_id": reply.id}

# Wishlist Endpoints
@app.get("/api/wishlist")
async def get_wishlist(current_user: User = Depends(get_current_user)):
    """Get user's wishlist"""
    wishlist = await db.wishlists.find_one({"user_id": current_user.id})
    
    if not wishlist or not wishlist.get("product_ids"):
        return {"products": [], "count": 0}
    
    # Get products and exclude MongoDB _id field
    products = await db.products.find({
        "id": {"$in": wishlist["product_ids"]},
        "is_sold": False
    }, {"_id": 0}).to_list(None)
    
    return {"products": products, "count": len(products)}

@app.post("/api/wishlist/{product_id}")
async def add_to_wishlist(product_id: str, current_user: User = Depends(get_current_user)):
    """Add product to wishlist"""
    # Check if product exists
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Get or create wishlist
    wishlist = await db.wishlists.find_one({"user_id": current_user.id})
    
    if not wishlist:
        wishlist_data = {
            "id": str(uuid.uuid4()),
            "user_id": current_user.id,
            "product_ids": [product_id],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await db.wishlists.insert_one(wishlist_data)
    else:
        # Add product if not already in wishlist
        if product_id not in wishlist.get("product_ids", []):
            await db.wishlists.update_one(
                {"user_id": current_user.id},
                {
                    "$push": {"product_ids": product_id},
                    "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
                }
            )
    
    return {"success": True, "message": "Added to wishlist"}

@app.delete("/api/wishlist/{product_id}")
async def remove_from_wishlist(product_id: str, current_user: User = Depends(get_current_user)):
    """Remove product from wishlist"""
    await db.wishlists.update_one(
        {"user_id": current_user.id},
        {
            "$pull": {"product_ids": product_id},
            "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
        }
    )
    
    return {"success": True, "message": "Removed from wishlist"}

@app.get("/api/wishlist/check/{product_id}")
async def check_wishlist(product_id: str, current_user: User = Depends(get_current_user)):
    """Check if product is in wishlist"""
    wishlist = await db.wishlists.find_one({"user_id": current_user.id})
    
    is_in_wishlist = False
    if wishlist and product_id in wishlist.get("product_ids", []):
        is_in_wishlist = True
    
    return {"is_in_wishlist": is_in_wishlist}

# Analytics Endpoints (Admin only)
@app.get("/api/admin/analytics/overview")
async def get_analytics_overview(
    days: int = 30,
    admin: User = Depends(get_admin_user)
):
    """Get analytics overview for dashboard"""
    from datetime import timedelta
    
    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    # Total stats
    total_users = await db.users.count_documents({})
    total_products = await db.products.count_documents({})
    total_active_products = await db.products.count_documents({"is_sold": False})
    total_transactions = await db.transactions.count_documents({"status": "completed"})
    
    # Revenue calculation
    completed_transactions = await db.transactions.find({"status": "completed"}).to_list(None)
    total_revenue = sum(t.get("total_amount", 0) for t in completed_transactions)
    total_commission = sum(t.get("commission", 0) for t in completed_transactions)
    
    # Average rating
    all_reviews = await db.reviews.find({}).to_list(None)
    avg_rating = sum(r.get("rating", 0) for r in all_reviews) / len(all_reviews) if all_reviews else 0
    
    # Recent period stats
    recent_users = await db.users.count_documents({
        "created_at": {"$gte": start_date.isoformat()}
    })
    recent_products = await db.products.count_documents({
        "created_at": {"$gte": start_date.isoformat()}
    })
    recent_transactions = await db.transactions.count_documents({
        "created_at": {"$gte": start_date.isoformat()},
        "status": "completed"
    })
    
    return {
        "total_users": total_users,
        "total_products": total_products,
        "total_active_products": total_active_products,
        "total_transactions": total_transactions,
        "total_revenue": round(total_revenue, 2),
        "total_commission": round(total_commission, 2),
        "average_rating": round(avg_rating, 2),
        "recent": {
            "users": recent_users,
            "products": recent_products,
            "transactions": recent_transactions,
            "days": days
        }
    }

@app.get("/api/admin/analytics/sales-trend")
async def get_sales_trend(
    days: int = 30,
    admin: User = Depends(get_admin_user)
):
    """Get sales trend data for charts"""
    from datetime import timedelta
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    # Get all completed transactions in date range
    transactions = await db.transactions.find({
        "status": "completed",
        "created_at": {"$gte": start_date.isoformat()}
    }).to_list(None)
    
    # Group by date
    daily_sales = {}
    for t in transactions:
        try:
            date_str = t.get("created_at", "")[:10]  # Get YYYY-MM-DD
            if date_str not in daily_sales:
                daily_sales[date_str] = {"count": 0, "revenue": 0, "commission": 0}
            daily_sales[date_str]["count"] += 1
            daily_sales[date_str]["revenue"] += t.get("total_amount", 0)
            daily_sales[date_str]["commission"] += t.get("commission", 0)
        except:
            continue
    
    # Convert to sorted list
    trend_data = []
    for date_str in sorted(daily_sales.keys()):
        trend_data.append({
            "date": date_str,
            "sales": daily_sales[date_str]["count"],
            "revenue": round(daily_sales[date_str]["revenue"], 2),
            "commission": round(daily_sales[date_str]["commission"], 2)
        })
    
    return trend_data

@app.get("/api/admin/analytics/category-distribution")
async def get_category_distribution(admin: User = Depends(get_admin_user)):
    """Get product distribution by category"""
    
    # Get all products with their categories
    products = await db.products.find({}, {"category": 1}).to_list(None)
    
    # Count by category
    category_counts = {}
    for p in products:
        cat = p.get("category", "Uncategorized")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    # Convert to list
    distribution = [
        {"name": cat, "value": count}
        for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    
    return distribution

@app.get("/api/admin/analytics/top-products")
async def get_top_products(
    limit: int = 10,
    metric: str = "views",
    admin: User = Depends(get_admin_user)
):
    """Get top products by views, sales, or rating"""
    
    if metric == "views":
        products = await db.products.find({}).sort("views", -1).limit(limit).to_list(None)
    elif metric == "sales":
        # Get products with most completed transactions
        transactions = await db.transactions.find({"status": "completed"}).to_list(None)
        product_sales = {}
        for t in transactions:
            pid = t.get("product_id")
            if pid:
                product_sales[pid] = product_sales.get(pid, 0) + 1
        
        # Get top product IDs
        top_ids = sorted(product_sales.keys(), key=lambda x: product_sales[x], reverse=True)[:limit]
        products = []
        for pid in top_ids:
            p = await db.products.find_one({"id": pid})
            if p:
                p["sales_count"] = product_sales[pid]
                products.append(p)
    else:
        # Get products with highest ratings
        products = await db.products.find({}).sort("rating_average", -1).limit(limit).to_list(None)
    
    # Remove MongoDB _id
    for p in products:
        if "_id" in p:
            del p["_id"]
    
    return products

@app.get("/api/admin/analytics/user-growth")
async def get_user_growth(
    days: int = 30,
    admin: User = Depends(get_admin_user)
):
    """Get user registration growth"""
    from datetime import timedelta
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    # Get all users in date range
    users = await db.users.find({
        "created_at": {"$gte": start_date.isoformat()}
    }).to_list(None)
    
    # Group by date
    daily_users = {}
    for u in users:
        try:
            date_str = u.get("created_at", "")[:10]
            daily_users[date_str] = daily_users.get(date_str, 0) + 1
        except:
            continue
    
    # Convert to sorted list
    growth_data = []
    for date_str in sorted(daily_users.keys()):
        growth_data.append({
            "date": date_str,
            "users": daily_users[date_str]
        })
    
    return growth_data

@app.get("/api/admin/analytics/seller-stats")
async def get_seller_stats(admin: User = Depends(get_admin_user)):
    """Get seller type distribution and top sellers"""
    
    # Seller type distribution
    total_sellers = await db.users.count_documents({})
    business_sellers = await db.users.count_documents({"is_business_seller": True})
    individual_sellers = total_sellers - business_sellers
    
    # Top sellers by completed transactions
    transactions = await db.transactions.find({"status": "completed"}).to_list(None)
    seller_sales = {}
    seller_revenue = {}
    
    for t in transactions:
        sid = t.get("seller_id")
        if sid:
            seller_sales[sid] = seller_sales.get(sid, 0) + 1
            seller_revenue[sid] = seller_revenue.get(sid, 0) + t.get("total_amount", 0)
    
    # Get top 10 sellers
    top_seller_ids = sorted(seller_sales.keys(), key=lambda x: seller_sales[x], reverse=True)[:10]
    top_sellers = []
    
    for sid in top_seller_ids:
        seller = await db.users.find_one({"id": sid})
        if seller:
            top_sellers.append({
                "id": sid,
                "name": seller.get("name"),
                "sales_count": seller_sales[sid],
                "revenue": round(seller_revenue[sid], 2),
                "is_business": seller.get("is_business_seller", False)
            })
    
    return {
        "distribution": {
            "individual": individual_sellers,
            "business": business_sellers
        },
        "top_sellers": top_sellers
    }


# Notification Endpoints
@app.get("/api/notifications")
async def get_notifications(
    limit: int = 50,
    current_user: User = Depends(get_current_user)
):
    """Get user's notifications"""
    notifications = await db.notifications.find({
        "user_id": current_user.id
    }).sort("created_at", -1).limit(limit).to_list(None)
    
    return notifications

@app.get("/api/notifications/unread-count")
async def get_unread_count(current_user: User = Depends(get_current_user)):
    """Get count of unread notifications"""
    count = await db.notifications.count_documents({
        "user_id": current_user.id,
        "read": False
    })
    
    return {"unread_count": count}

@app.put("/api/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user)
):
    """Mark a notification as read"""
    notification = await db.notifications.find_one({"id": notification_id})
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    # Verify user owns this notification
    if notification["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    await db.notifications.update_one(
        {"id": notification_id},
        {"$set": {"read": True}}
    )
    
    return {"success": True}

@app.put("/api/notifications/mark-all-read")
async def mark_all_notifications_read(current_user: User = Depends(get_current_user)):
    """Mark all notifications as read"""
    result = await db.notifications.update_many(
        {"user_id": current_user.id, "read": False},
        {"$set": {"read": True}}
    )
    
    return {"message": "All notifications marked as read", "updated_count": result.modified_count}

@app.delete("/api/notifications/{notification_id}")
async def delete_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a specific notification"""
    notification = await db.notifications.find_one({"id": notification_id})
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    # Verify user owns this notification
    if notification["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    await db.notifications.delete_one({"id": notification_id})
    
    return {"message": "Notification deleted successfully"}

@app.delete("/api/notifications/clear-all")
async def clear_all_notifications(current_user: User = Depends(get_current_user)):
    """Delete all notifications for current user"""
    result = await db.notifications.delete_many({"user_id": current_user.id})
    
    return {"message": "All notifications cleared", "deleted_count": result.deleted_count}

@app.get("/api/notifications/preferences")
async def get_notification_preferences(current_user: User = Depends(get_current_user)):
    """Get user's notification preferences"""
    prefs = await db.notification_preferences.find_one({"user_id": current_user.id})
    
    # If no preferences exist, return default
    if not prefs:
        default_prefs = NotificationPreferences(user_id=current_user.id)
        prefs_dict = default_prefs.dict()
        await db.notification_preferences.insert_one(prefs_dict)
        return default_prefs
    
    # Remove MongoDB _id
    if "_id" in prefs:
        del prefs["_id"]
    
    return prefs

@app.put("/api/notifications/preferences")
async def update_notification_preferences(
    preferences: dict,
    current_user: User = Depends(get_current_user)
):
    """Update user's notification preferences"""
    # Check if preferences exist
    existing = await db.notification_preferences.find_one({"user_id": current_user.id})
    
    if existing:
        # Update existing preferences
        await db.notification_preferences.update_one(
            {"user_id": current_user.id},
            {"$set": preferences}
        )
    else:
        # Create new preferences
        preferences["user_id"] = current_user.id
        await db.notification_preferences.insert_one(preferences)
    
    return {"message": "Preferences updated successfully"}


@app.put("/api/notifications/mark-all-read")
async def mark_all_notifications_read(current_user: User = Depends(get_current_user)):
    """Mark all notifications as read"""
    result = await db.notifications.update_many(
        {"user_id": current_user.id, "read": False},
        {"$set": {"read": True}}
    )
    
    return {"success": True, "updated_count": result.modified_count}

# Review Endpoints
@app.post("/api/products/{product_id}/reviews", response_model=Review)
async def create_product_review(
    product_id: str,
    review_data: ReviewCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a review for a product (only if user purchased it)"""
    # Check if product exists
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if user purchased this product
    transaction = await db.transactions.find_one({
        "product_id": product_id,
        "buyer_id": current_user.id,
        "status": {"$in": ["completed", "held"]}  # Allow reviews for completed or in escrow
    })
    
    if not transaction:
        raise HTTPException(
            status_code=403, 
            detail="You can only review products you have purchased"
        )
    
    # Check if user already reviewed this product
    existing_review = await db.reviews.find_one({
        "product_id": product_id,
        "user_id": current_user.id
    })
    
    if existing_review:
        raise HTTPException(
            status_code=400,
            detail="You have already reviewed this product"
        )
    
    # Create review
    review = Review(
        product_id=product_id,
        user_id=current_user.id,
        seller_id=product["seller_id"],
        rating=review_data.rating,
        comment=review_data.comment
    )
    
    review_dict = review.dict()
    review_dict["created_at"] = review_dict["created_at"].isoformat()
    
    await db.reviews.insert_one(review_dict)
    
    # Send notification to seller
    asyncio.create_task(create_notification(
        user_id=product["seller_id"],
        notification_type="review",
        title="New Review Received",
        message=f"You received a {review_data.rating}-star review for '{product['title']}'",
        link=f"/product/{product_id}"
    ))
    
    return review

@app.get("/api/products/{product_id}/reviews")
async def get_product_reviews(product_id: str):
    """Get all reviews for a product"""
    reviews = await db.reviews.find({"product_id": product_id}).sort("created_at", -1).to_list(None)
    
    # Enrich with user info
    enriched_reviews = []
    for review in reviews:
        user = await db.users.find_one({"id": review["user_id"]})
        if user:
            enriched_reviews.append({
                **review,
                "user_name": user["name"],
                "user_image": user.get("profile_image", "")
            })
    
    return enriched_reviews

@app.get("/api/products/{product_id}/rating")
async def get_product_rating(product_id: str):
    """Get average rating for a product"""
    reviews = await db.reviews.find({"product_id": product_id}).to_list(None)
    
    if not reviews:
        return {
            "average_rating": 0,
            "total_reviews": 0
        }
    
    total_rating = sum(r["rating"] for r in reviews)
    average_rating = total_rating / len(reviews)
    
    return {
        "average_rating": round(average_rating, 2),
        "total_reviews": len(reviews)
    }

@app.get("/api/sellers/{seller_id}/rating")
async def get_seller_rating(seller_id: str):
    """Get average rating for a seller (across all their products)"""
    reviews = await db.reviews.find({"seller_id": seller_id}).to_list(None)
    
    if not reviews:
        return {
            "average_rating": 0,
            "total_reviews": 0,
            "rating_distribution": {
                "5": 0,
                "4": 0,
                "3": 0,
                "2": 0,
                "1": 0
            }
        }
    
    total_rating = sum(r["rating"] for r in reviews)
    average_rating = total_rating / len(reviews)
    
    # Calculate rating distribution
    rating_distribution = {
        "5": len([r for r in reviews if r["rating"] == 5]),
        "4": len([r for r in reviews if r["rating"] == 4]),
        "3": len([r for r in reviews if r["rating"] == 3]),
        "2": len([r for r in reviews if r["rating"] == 2]),
        "1": len([r for r in reviews if r["rating"] == 1])
    }
    
    return {
        "average_rating": round(average_rating, 2),
        "total_reviews": len(reviews),
        "rating_distribution": rating_distribution
    }

@app.delete("/api/reviews/{review_id}")
async def delete_review(review_id: str, current_user: User = Depends(get_current_user)):
    """Delete own review"""
    review = await db.reviews.find_one({"id": review_id})
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    
    # Verify user owns this review
    if review["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this review")
    
    await db.reviews.delete_one({"id": review_id})
    
    return {"success": True, "message": "Review deleted"}

# Messages endpoints
@app.post("/api/messages", response_model=Message)
async def send_message(message_data: MessageCreate, current_user: User = Depends(get_current_user)):
    # Verify product exists
    product = await db.products.find_one({"id": message_data.product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    message = Message(
        sender_id=current_user.id,
        recipient_id=message_data.recipient_id,
        product_id=message_data.product_id,
        content=message_data.content
    )
    
    message_dict = message.dict()
    message_dict["created_at"] = message_dict["created_at"].isoformat()
    
    await db.messages.insert_one(message_dict)
    return message

@app.get("/api/messages/conversations")
async def get_conversations(current_user: User = Depends(get_current_user)):
    """Get all conversations for the current user (organized by product and other user)"""
    # Get all conversations where user is buyer or seller
    conversations = await db.conversations.find({
        "$or": [
            {"buyer_id": current_user.id},
            {"seller_id": current_user.id}
        ]
    }).sort("updated_at", -1).to_list(None)
    
    # Enrich with product and user info
    enriched_convs = []
    for conv in conversations:
        # Get product info
        product = await db.products.find_one({"id": conv["product_id"]})
        if not product:
            continue
        
        # Get other user info
        other_user_id = conv["seller_id"] if conv["buyer_id"] == current_user.id else conv["buyer_id"]
        other_user = await db.users.find_one({"id": other_user_id})
        if not other_user:
            continue
        
        # Get unread count for current user
        is_buyer = conv["buyer_id"] == current_user.id
        unread_count = conv.get("buyer_unread_count" if is_buyer else "seller_unread_count", 0)
        
        enriched_convs.append({
            "id": conv["id"],
            "product_id": conv["product_id"],
            "product_title": product["title"],
            "product_image": product.get("images", ["/static/placeholder.jpg"])[0],
            "other_user_id": other_user_id,
            "other_user_name": other_user["name"],
            "other_user_image": other_user.get("profile_image", ""),
            "last_message": conv.get("last_message", ""),
            "last_message_at": conv.get("last_message_at"),
            "unread_count": unread_count,
            "updated_at": conv["updated_at"]
        })
    
    return enriched_convs

# Conversation endpoints
@app.post("/api/conversations")
async def create_conversation(conv_data: ConversationCreate, current_user: User = Depends(get_current_user)):
    """Create or get existing conversation and send initial message"""
    # Get product to determine buyer/seller
    product = await db.products.find_one({"id": conv_data.product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Determine buyer and seller
    if product["seller_id"] == current_user.id:
        # Seller is initiating (unusual but allowed)
        buyer_id = conv_data.recipient_id
        seller_id = current_user.id
    else:
        # Buyer is initiating (normal case)
        buyer_id = current_user.id
        seller_id = conv_data.recipient_id
    
    # Check if conversation already exists
    existing_conv = await db.conversations.find_one({
        "product_id": conv_data.product_id,
        "buyer_id": buyer_id,
        "seller_id": seller_id
    })
    
    if existing_conv:
        conversation_id = existing_conv["id"]
    else:
        # Create new conversation
        conversation = Conversation(
            product_id=conv_data.product_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            last_message=conv_data.initial_message[:100],
            last_message_at=datetime.now(timezone.utc),
            buyer_unread_count=0 if buyer_id == current_user.id else 1,
            seller_unread_count=0 if seller_id == current_user.id else 1
        )
        
        conv_dict = conversation.dict()
        conv_dict["created_at"] = conv_dict["created_at"].isoformat()
        conv_dict["updated_at"] = conv_dict["updated_at"].isoformat()
        if conv_dict["last_message_at"]:
            conv_dict["last_message_at"] = conv_dict["last_message_at"].isoformat()
        
        await db.conversations.insert_one(conv_dict)
        conversation_id = conversation.id
    
    # Send initial message
    message = Message(
        sender_id=current_user.id,
        recipient_id=conv_data.recipient_id,
        product_id=conv_data.product_id,
        content=conv_data.initial_message
    )
    
    message_dict = message.dict()
    message_dict["created_at"] = message_dict["created_at"].isoformat()
    message_dict["conversation_id"] = conversation_id
    
    await db.messages.insert_one(message_dict)
    
    return {"conversation_id": conversation_id, "message": "Conversation created"}

@app.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str, current_user: User = Depends(get_current_user)):
    """Get all messages in a conversation"""
    # Verify user is part of this conversation
    conversation = await db.conversations.find_one({"id": conversation_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if conversation["buyer_id"] != current_user.id and conversation["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this conversation")
    
    # Get messages
    messages = await db.messages.find({
        "conversation_id": conversation_id
    }).sort("created_at", 1).to_list(None)
    
    # Remove MongoDB _id
    for msg in messages:
        if "_id" in msg:
            del msg["_id"]
    
    # Auto mark as read when fetching
    await db.messages.update_many(
        {
            "conversation_id": conversation_id,
            "sender_id": {"$ne": current_user.id},
            "read": False
        },
        {"$set": {
            "read": True,
            "read_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    # Reset unread count
    is_buyer = conversation["buyer_id"] == current_user.id
    await db.conversations.update_one(
        {"id": conversation_id},
        {"$set": {
            "buyer_unread_count" if is_buyer else "seller_unread_count": 0
        }}
    )
    
    return messages

@app.post("/api/conversations/{conversation_id}/messages")
async def send_message_in_conversation(
    conversation_id: str,
    message_data: dict,
    current_user: User = Depends(get_current_user)
):
    """Send a message in an existing conversation"""
    # Verify conversation exists and user is part of it
    conversation = await db.conversations.find_one({"id": conversation_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if conversation["buyer_id"] != current_user.id and conversation["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to send message in this conversation")
    
    content = message_data.get("content", "")
    attachments = message_data.get("attachments", [])
    
    # Create message
    message = Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        message=content,
        attachments=attachments
    )
    
    message_dict = message.dict()
    message_dict["created_at"] = message_dict["created_at"].isoformat()
    
    await db.messages.insert_one(message_dict)
    
    # Update conversation
    is_buyer = conversation["buyer_id"] == current_user.id
    last_msg_preview = content[:100] if content else "[Attachment]"
    
    await db.conversations.update_one(
        {"id": conversation_id},
        {
            "$set": {
                "last_message": last_msg_preview,
                "last_message_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                # Reset sender's typing status
                "buyer_typing" if is_buyer else "seller_typing": False
            },
            "$inc": {
                "seller_unread_count" if is_buyer else "buyer_unread_count": 1
            }
        }
    )
    
    # Send notification to recipient
    recipient_id = conversation["seller_id"] if is_buyer else conversation["buyer_id"]
    product = await db.products.find_one({"id": conversation["product_id"]})
    asyncio.create_task(create_notification(
        user_id=recipient_id,
        notification_type="message",
        title="New Message",
        message=f"You have a new message about '{product['title'] if product else 'a product'}'",
        link="/messages"
    ))
    
    return {"message": "Message sent", "message_id": message.id}


@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    """Upload a file (image or document) for messaging"""
    from pathlib import Path
    
    # Check file size (max 10MB)
    contents = await file.read()
    file_size = len(contents)
    if file_size > 10 * 1024 * 1024:  # 10MB
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10MB")
    
    # Check file type
    allowed_types = {
        'image/jpeg', 'image/png', 'image/gif', 'image/webp',
        'application/pdf', 'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    }
    
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="File type not allowed")
    
    # Create uploads directory if it doesn't exist
    upload_dir = Path("/app/uploads")
    upload_dir.mkdir(exist_ok=True)
    
    # Generate unique filename
    file_extension = Path(file.filename).suffix
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = upload_dir / unique_filename
    
    # Save file
    with open(file_path, "wb") as f:
        f.write(contents)
    
    # Determine file type
    file_type = "image" if file.content_type.startswith("image/") else "file"
    
    return {
        "url": f"/uploads/{unique_filename}",
        "name": file.filename,
        "size": file_size,
        "type": file_type
    }

@app.put("/api/conversations/{conversation_id}/typing")
async def update_typing_status(
    conversation_id: str,
    is_typing: bool,
    current_user: User = Depends(get_current_user)
):
    """Update typing status for a conversation"""
    conversation = await db.conversations.find_one({"id": conversation_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if conversation["buyer_id"] != current_user.id and conversation["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    is_buyer = conversation["buyer_id"] == current_user.id
    field_name = "buyer_typing" if is_buyer else "seller_typing"
    timestamp_field = "buyer_typing_at" if is_buyer else "seller_typing_at"
    
    await db.conversations.update_one(
        {"id": conversation_id},
        {"$set": {
            field_name: is_typing,
            timestamp_field: datetime.now(timezone.utc).isoformat() if is_typing else None
        }}
    )
    
    return {"message": "Typing status updated"}

@app.put("/api/conversations/{conversation_id}/messages/mark-read")
async def mark_messages_read(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """Mark all messages in a conversation as read"""
    conversation = await db.conversations.find_one({"id": conversation_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if conversation["buyer_id"] != current_user.id and conversation["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Mark all unread messages as read
    await db.messages.update_many(
        {
            "conversation_id": conversation_id,
            "sender_id": {"$ne": current_user.id},
            "read": False
        },
        {"$set": {
            "read": True,
            "read_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    # Reset unread count
    is_buyer = conversation["buyer_id"] == current_user.id
    await db.conversations.update_one(
        {"id": conversation_id},
        {"$set": {
            "buyer_unread_count" if is_buyer else "seller_unread_count": 0
        }}
    )
    
    return {"message": "Messages marked as read"}

# Transaction endpoints
@app.post("/api/transactions", response_model=Transaction)
async def create_transaction(transaction_data: TransactionCreate, current_user: User = Depends(get_current_user)):
    """Create a new transaction (purchase)"""
    # Get product details
    product = await db.products.find_one({"id": transaction_data.product_id, "is_sold": False})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found or already sold")
    
    # Can't buy your own product
    if product["seller_id"] == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot buy your own product")
    
    # Calculate commission (5%)
    commission = product["price"] * 0.05
    
    # Create transaction
    transaction = Transaction(
        product_id=product["id"],
        buyer_id=current_user.id,
        seller_id=product["seller_id"],
        amount=product["price"],
        commission=commission,
        payment_provider=transaction_data.payment_provider,
        status="held"  # Payment held in escrow
    )
    
    transaction_dict = transaction.dict()
    transaction_dict["created_at"] = transaction_dict["created_at"].isoformat()
    
    await db.transactions.insert_one(transaction_dict)
    
    # Mark product as sold
    await db.products.update_one(
        {"id": product["id"]},
        {"$set": {"is_sold": True}}
    )
    
    # Remove from cart if it was there
    await db.carts.update_one(
        {"user_id": current_user.id},
        {"$pull": {"items": {"product_id": product["id"]}}}
    )
    
    # Send purchase confirmation email
    try:
        asyncio.create_task(send_purchase_confirmation_email(
            current_user, 
            product["title"], 
            product["price"], 
            'nl'
        ))
    except Exception as e:
        logging.error(f"Failed to send purchase confirmation email: {str(e)}")
    
    return transaction

@app.get("/api/transactions")
async def get_user_transactions(current_user: User = Depends(get_current_user)):
    """Get all transactions for the current user (as buyer or seller)"""
    transactions = await db.transactions.find({
        "$or": [
            {"buyer_id": current_user.id},
            {"seller_id": current_user.id}
        ]
    }).sort("created_at", -1).to_list(None)
    
    # Enrich transactions with product and user information
    enriched_transactions = []
    for transaction in transactions:
        # Remove MongoDB ObjectId to avoid serialization issues
        if "_id" in transaction:
            del transaction["_id"]
            
        # Get product info
        product = await db.products.find_one({"id": transaction["product_id"]})
        
        # Get buyer and seller info
        buyer = await db.users.find_one({"id": transaction["buyer_id"]})
        seller = await db.users.find_one({"id": transaction["seller_id"]})
        
        enriched_transaction = {
            **transaction,
            "product_title": product["title"] if product else "Unknown Product",
            "product_images": product.get("images", []) if product else [],
            "buyer_name": buyer["name"] if buyer else "Unknown Buyer",
            "seller_name": seller["name"] if seller else "Unknown Seller",
            "user_role": "buyer" if transaction["buyer_id"] == current_user.id else "seller"
        }
        enriched_transactions.append(enriched_transaction)
    
    return enriched_transactions
@app.put("/api/transactions/{transaction_id}/complete")
async def complete_transaction(transaction_id: str, current_user: User = Depends(get_current_user)):
    """Complete a transaction (release escrow payment)"""
    transaction = await db.transactions.find_one({"id": transaction_id})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Only buyer or seller can complete
    if transaction["buyer_id"] != current_user.id and transaction["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to complete this transaction")
    
    if transaction["status"] != "held":
        raise HTTPException(status_code=400, detail="Transaction cannot be completed")
    
    # Update transaction status
    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat()
            }
        }
    )
    
    return {"success": True, "message": "Transaction completed successfully"}

# ============================================
# STRIPE PAYMENT ENDPOINTS
# ============================================

@app.post("/api/payments/create-checkout")
async def create_payment_checkout(
    product_id: str,
    origin_url: str,
    current_user: User = Depends(get_current_user)
):
    """Create Stripe checkout session for product purchase"""
    global stripe_checkout
    
    # Get product details
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if product["seller_id"] == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot buy your own product")
    
    # Calculate amounts: Product Price + Platform Commission (5%)
    product_price = float(product["price"])
    commission_rate = 0.05  # 5% platform fee
    
    commission = product_price * commission_rate
    total_amount = product_price + commission
    
    # Initialize Stripe checkout if not already done
    if not stripe_checkout:
        host_url = origin_url
        webhook_url = f"{host_url}/api/webhook/stripe"
        stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    
    # Create transaction record first
    transaction_id = str(uuid.uuid4())
    # Note: We'll calculate the exact auto-release time after delivery confirmation
    
    transaction_dict = {
        "id": transaction_id,
        "product_id": product_id,
        "buyer_id": current_user.id,
        "seller_id": product["seller_id"],
        "amount": product_price,
        "commission": commission,
        "commission_rate": commission_rate,
        "total_amount": total_amount,
        "status": "pending",
        "payment_provider": "stripe",
        "delivery_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.transactions.insert_one(transaction_dict)
    
    # Create checkout session
    success_url = f"{origin_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin_url}/browse"
    
    checkout_request = CheckoutSessionRequest(
        amount=total_amount,
        currency="eur",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "transaction_id": transaction_id,
            "product_id": product_id,
            "buyer_id": current_user.id,
            "seller_id": product["seller_id"]
        }
    )
    
    session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_request)
    
    # Create payment transaction record
    payment_transaction_dict = {
        "id": str(uuid.uuid4()),
        "session_id": session.session_id,
        "transaction_id": transaction_id,
        "product_id": product_id,
        "buyer_id": current_user.id,
        "seller_id": product["seller_id"],
        "amount": total_amount,
        "currency": "eur",
        "payment_status": "pending",
        "metadata": checkout_request.metadata,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.payment_transactions.insert_one(payment_transaction_dict)
    
    # Update transaction with session ID
    await db.transactions.update_one(
        {"id": transaction_id},
        {"$set": {"payment_session_id": session.session_id}}
    )
    
    return {
        "checkout_url": session.url,
        "session_id": session.session_id,
        "transaction_id": transaction_id
    }

@app.post("/api/payments/create-checkout-cart")
async def create_cart_checkout(
    origin_url: str,
    current_user: User = Depends(get_current_user)
):
    """Create Stripe checkout session for entire cart"""
    global stripe_checkout
    
    # Get user's cart
    cart = await db.carts.find_one({"user_id": current_user.id})
    if not cart or not cart.get("items"):
        raise HTTPException(status_code=400, detail="Cart is empty")
    
    # Get all products in cart
    product_ids = [item["product_id"] for item in cart["items"]]
    products = await db.products.find({"id": {"$in": product_ids}, "is_sold": False}).to_list(None)
    
    if not products:
        raise HTTPException(status_code=404, detail="No available products in cart")
    
    # Check that user isn't buying their own products
    for product in products:
        if product["seller_id"] == current_user.id:
            raise HTTPException(status_code=400, detail=f"Cannot buy your own product: {product['title']}")
    
    # Calculate total amounts
    total_product_price = sum(float(p["price"]) for p in products)
    commission_rate = 0.05  # 5% platform fee
    total_commission = total_product_price * commission_rate
    grand_total = total_product_price + total_commission
    
    # Initialize Stripe checkout if not already done
    if not stripe_checkout:
        host_url = origin_url
        webhook_url = f"{host_url}/api/webhook/stripe"
        stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    
    # Create transaction records for each product
    transaction_ids = []
    for product in products:
        transaction_id = str(uuid.uuid4())
        product_price = float(product["price"])
        commission = product_price * commission_rate
        total_amount = product_price + commission
        
        transaction_dict = {
            "id": transaction_id,
            "product_id": product["id"],
            "buyer_id": current_user.id,
            "seller_id": product["seller_id"],
            "amount": product_price,
            "commission": commission,
            "commission_rate": commission_rate,
            "total_amount": total_amount,
            "status": "pending",
            "payment_provider": "stripe",
            "delivery_status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cart_checkout": True  # Flag to indicate this was part of cart checkout
        }
        
        await db.transactions.insert_one(transaction_dict)
        transaction_ids.append(transaction_id)
    
    # Create single checkout session for all items
    success_url = f"{origin_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}&cart=true"
    cancel_url = f"{origin_url}/cart"
    
    checkout_request = CheckoutSessionRequest(
        amount=grand_total,
        currency="eur",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "transaction_ids": ",".join(transaction_ids),  # Store all transaction IDs
            "buyer_id": current_user.id,
            "cart_checkout": "true",
            "product_count": str(len(products))
        }
    )
    
    session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_request)
    
    # Create payment transaction record
    payment_transaction_dict = {
        "id": str(uuid.uuid4()),
        "session_id": session.session_id,
        "transaction_ids": transaction_ids,  # Store all related transaction IDs
        "buyer_id": current_user.id,
        "amount": grand_total,
        "currency": "eur",
        "payment_status": "pending",
        "metadata": checkout_request.metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cart_checkout": True
    }
    
    await db.payment_transactions.insert_one(payment_transaction_dict)
    
    # Update all transactions with session ID
    await db.transactions.update_many(
        {"id": {"$in": transaction_ids}},
        {"$set": {"payment_session_id": session.session_id}}
    )
    
    # Clear the cart after creating checkout
    await db.carts.update_one(
        {"user_id": current_user.id},
        {"$set": {"items": [], "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    return {
        "checkout_url": session.url,
        "session_id": session.session_id,
        "transaction_ids": transaction_ids,
        "total_amount": grand_total,
        "product_count": len(products)
    }

@app.get("/api/payments/checkout-status/{session_id}")
async def get_checkout_status(
    session_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get payment status for a checkout session"""
    global stripe_checkout
    
    if not stripe_checkout:
        raise HTTPException(status_code=500, detail="Stripe not initialized")
    
    # Get checkout status from Stripe
    checkout_status: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)
    
    # Update payment transaction
    payment_transaction = await db.payment_transactions.find_one({"session_id": session_id})
    if not payment_transaction:
        raise HTTPException(status_code=404, detail="Payment transaction not found")
    
    # Verify user authorization
    if payment_transaction["buyer_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Update payment status only if changed and not already processed
    if payment_transaction["payment_status"] != checkout_status.payment_status:
        await db.payment_transactions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "payment_status": checkout_status.payment_status,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        
        # Update transaction status based on payment status
        transaction_id = payment_transaction["transaction_id"]
        
        if checkout_status.payment_status == "paid":
            # Payment successful - hold funds in escrow
            await db.transactions.update_one(
                {"id": transaction_id},
                {"$set": {"status": "held"}}
            )
            
            # Create invoice for the paid transaction
            try:
                # Check if invoice already exists
                existing_invoice = await db.invoices.find_one({"transaction_id": transaction_id})
                if not existing_invoice:
                    invoice_number = await generate_invoice_number()
                    
                    # Get transaction details
                    transaction = await db.transactions.find_one({"id": transaction_id})
                    if transaction:
                        # Calculate VAT (21% of total amount)
                        vat_amount = transaction["total_amount"] * 0.21
                        
                        invoice = Invoice(
                            invoice_number=invoice_number,
                            transaction_id=transaction_id,
                            buyer_id=transaction["buyer_id"],
                            seller_id=transaction["seller_id"],
                            product_id=transaction["product_id"],
                            amount=transaction["amount"],
                            commission=transaction["commission"],
                            vat_amount=vat_amount,
                            total_amount=transaction["total_amount"],
                            payment_method="stripe",
                            payment_status="paid",
                            invoice_status="paid"
                        )
                        
                        invoice_dict = invoice.dict()
                        invoice_dict["invoice_date"] = invoice_dict["invoice_date"].isoformat()
                        invoice_dict["created_at"] = invoice_dict["created_at"].isoformat()
                        invoice_dict["updated_at"] = invoice_dict["updated_at"].isoformat()
                        
                        await db.invoices.insert_one(invoice_dict)
                        logging.info(f"Invoice created: {invoice_number} for transaction {transaction_id}")
            except Exception as e:
                logging.error(f"Failed to create invoice: {str(e)}")
            
            # Send purchase confirmation email
            if sg:
                buyer = await db.users.find_one({"id": payment_transaction["buyer_id"]})
                product = await db.products.find_one({"id": payment_transaction["product_id"]})
                if buyer and product:
                    message = Mail(
                        from_email=SENDER_EMAIL,
                        to_emails=buyer["email"],
                        subject=f"Purchase Confirmation - {product['title']}",
                        html_content=f"""
                        <h2>Thank you for your purchase!</h2>
                        <p>Your payment has been received and held securely in escrow.</p>
                        <p><strong>Product:</strong> {product['title']}</p>
                        <p><strong>Amount:</strong> €{payment_transaction['amount']:.2f}</p>
                        <p>The seller will contact you for delivery arrangements.</p>
                        <p>Once you receive the item, please confirm delivery in your orders.</p>
                        """
                    )
                    try:
                        sg.send(message)
                    except Exception as e:
                        logging.error(f"Failed to send purchase confirmation email: {str(e)}")
        
        elif checkout_status.payment_status in ["failed", "expired"]:
            # Payment failed - update transaction
            await db.transactions.update_one(
                {"id": transaction_id},
                {"$set": {"status": "cancelled"}}
            )
    
    return {
        "status": checkout_status.status,
        "payment_status": checkout_status.payment_status,
        "amount_total": checkout_status.amount_total,
        "currency": checkout_status.currency,
        "transaction_id": payment_transaction["transaction_id"]
    }

@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    global stripe_checkout
    
    if not stripe_checkout:
        # Initialize stripe checkout for webhook handling
        host_url = str(request.base_url)
        webhook_url = f"{host_url}/api/webhook/stripe"
        stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    
    try:
        webhook_request_body = await request.body()
        stripe_signature = request.headers.get("Stripe-Signature")
        
        webhook_response = await stripe_checkout.handle_webhook(
            webhook_request_body,
            stripe_signature
        )
        
        # Log webhook event
        logging.info(f"Stripe webhook received: {webhook_response.event_type}, session: {webhook_response.session_id}")
        
        return {"status": "success", "event_id": webhook_response.event_id}
    except Exception as e:
        logging.error(f"Stripe webhook error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# ============================================
# DELIVERY CONFIRMATION & ESCROW RELEASE
# ============================================

@app.post("/api/transactions/{transaction_id}/confirm-delivery")
async def confirm_delivery(
    transaction_id: str,
    confirmation: DeliveryConfirmation,
    current_user: User = Depends(get_current_user)
):
    """Buyer confirms delivery or raises dispute"""
    transaction = await db.transactions.find_one({"id": transaction_id})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Only buyer can confirm delivery
    if transaction["buyer_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if transaction["status"] != "held":
        raise HTTPException(status_code=400, detail="Transaction is not in escrow")
    
    if confirmation.confirmation_type == "delivered":
        # Buyer confirms delivery - release funds after 3 days
        delivery_confirmed_at = datetime.now(timezone.utc)
        auto_release_at = datetime.now(timezone.utc)
        # Add 3 days for auto-release
        from datetime import timedelta
        auto_release_at = delivery_confirmed_at + timedelta(days=3)
        
        await db.transactions.update_one(
            {"id": transaction_id},
            {
                "$set": {
                    "delivery_status": "confirmed",
                    "delivery_confirmed_at": delivery_confirmed_at.isoformat(),
                    "auto_release_at": auto_release_at.isoformat()
                }
            }
        )
        
        # Schedule automatic fund release (in production, use a job scheduler)
        # For now, we'll mark it for manual processing or cron job
        
        # Send delivery confirmation email to seller
        try:
            product = await db.products.find_one({"id": transaction["product_id"]})
            seller = await db.users.find_one({"id": transaction["seller_id"]})
            if seller and product:
                asyncio.create_task(send_delivery_confirmation_email(
                    User(**seller),
                    product["title"],
                    transaction_id,
                    'nl'  # Default to Dutch
                ))
        except Exception as e:
            logging.error(f"Failed to send delivery confirmation email: {str(e)}")
        
        return {
            "success": True,
            "message": "Delivery confirmed. Funds will be released to seller in 3 days.",
            "auto_release_date": auto_release_at.isoformat()
        }
    
    elif confirmation.confirmation_type == "dispute":
        # Buyer raises dispute
        await db.transactions.update_one(
            {"id": transaction_id},
            {
                "$set": {
                    "delivery_status": "disputed",
                    "delivery_confirmed_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        
        return {
            "success": True,
            "message": "Dispute raised. Our team will review and contact you."
        }

@app.post("/api/transactions/{transaction_id}/release-funds")
async def release_funds_to_seller(transaction_id: str):
    """
    Release funds to seller (called by cron job or after auto-release period)
    This should be protected in production with an API key or internal call only
    """
    transaction = await db.transactions.find_one({"id": transaction_id})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction["status"] != "held":
        raise HTTPException(status_code=400, detail="Transaction is not in held status")
    
    # Check if auto-release period has passed
    if transaction.get("delivery_status") == "confirmed":
        auto_release_at = transaction.get("auto_release_at")
        if auto_release_at:
            from datetime import datetime as dt_parse
            release_date = dt_parse.fromisoformat(auto_release_at)
            if datetime.now(timezone.utc) < release_date:
                raise HTTPException(status_code=400, detail="Auto-release period has not passed yet")
    
    # Release funds
    seller_amount = transaction["amount"]
    
    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat()
            }
        }
    )
    
    # Create invoice for the completed transaction
    try:
        # Check if invoice already exists
        existing_invoice = await db.invoices.find_one({"transaction_id": transaction_id})
        if not existing_invoice:
            invoice_number = await generate_invoice_number()
            
            # Calculate VAT (21% of total amount)
            vat_amount = transaction["total_amount"] * 0.21
            
            invoice = Invoice(
                invoice_number=invoice_number,
                transaction_id=transaction_id,
                buyer_id=transaction["buyer_id"],
                seller_id=transaction["seller_id"],
                product_id=transaction["product_id"],
                amount=transaction["amount"],
                commission=transaction["commission"],
                vat_amount=vat_amount,
                total_amount=transaction["total_amount"],
                payment_method="stripe",
                payment_status="paid",
                invoice_status="paid"
            )
            
            invoice_dict = invoice.dict()
            invoice_dict["invoice_date"] = invoice_dict["invoice_date"].isoformat()
            invoice_dict["created_at"] = invoice_dict["created_at"].isoformat()
            invoice_dict["updated_at"] = invoice_dict["updated_at"].isoformat()
            
            await db.invoices.insert_one(invoice_dict)
            logging.info(f"Invoice created: {invoice_number} for transaction {transaction_id}")
    except Exception as e:
        logging.error(f"Failed to create invoice: {str(e)}")
    
    # In production, transfer funds to seller's account via Stripe Connect or payout API
    # For now, just mark as completed
    
    # Send notification to seller
    if sg:
        seller = await db.users.find_one({"id": transaction["seller_id"]})
        product = await db.products.find_one({"id": transaction["product_id"]})
        if seller and product:
            message = Mail(
                from_email=SENDER_EMAIL,
                to_emails=seller["email"],
                subject=f"Payment Received - {product['title']}",
                html_content=f"""
                <h2>Payment Released!</h2>
                <p>The buyer has confirmed delivery and funds have been released to your account.</p>
                <p><strong>Product:</strong> {product['title']}</p>
                <p><strong>Amount:</strong> €{seller_amount:.2f}</p>
                <p>Thank you for selling on Relivv!</p>
                """
            )
            try:
                sg.send(message)
            except Exception as e:
                logging.error(f"Failed to send seller notification email: {str(e)}")
    
    return {
        "success": True,
        "message": "Funds released to seller",
        "amount": seller_amount
    }

@app.post("/api/transactions/{transaction_id}/cancel")
async def cancel_transaction_and_refund(
    transaction_id: str,
    current_user: User = Depends(get_current_user)
):
    """Cancel transaction and refund buyer (with commission)"""
    transaction = await db.transactions.find_one({"id": transaction_id})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Only buyer or seller can cancel
    if transaction["buyer_id"] != current_user.id and transaction["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if transaction["status"] not in ["pending", "held"]:
        raise HTTPException(status_code=400, detail="Transaction cannot be cancelled")
    
    # Mark as cancelled and refunded
    payment_transaction = await db.payment_transactions.find_one({"transaction_id": transaction_id})
    if payment_transaction:
        # Update payment transaction
        await db.payment_transactions.update_one(
            {"transaction_id": transaction_id},
            {
                "$set": {
                    "payment_status": "refunded",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
    
    # In production, process actual refund via Stripe API
    # Including the commission back to buyer (full refund)
    refund_amount = transaction["amount"] + transaction["commission"]
    
    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "refunded",
                "refunded_at": datetime.now(timezone.utc).isoformat()
            }
        }
    )
    
    # Update invoice status if exists
    try:
        invoice = await db.invoices.find_one({"transaction_id": transaction_id})
        if invoice:
            await db.invoices.update_one(
                {"transaction_id": transaction_id},
                {
                    "$set": {
                        "invoice_status": "refunded",
                        "payment_status": "refunded",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            )
            logging.info(f"Invoice {invoice['invoice_number']} marked as refunded")
    except Exception as e:
        logging.error(f"Failed to update invoice status: {str(e)}")
    
    # Send refund confirmation email
    if sg:
        buyer = await db.users.find_one({"id": transaction["buyer_id"]})
        if buyer:
            message = Mail(
                from_email=SENDER_EMAIL,
                to_emails=buyer["email"],
                subject="Refund Processed - Relivv",
                html_content=f"""
                <h2>Refund Processed</h2>
                <p>Your transaction has been cancelled and a full refund has been initiated.</p>
                <p><strong>Product Amount:</strong> €{transaction['amount']:.2f}</p>
                <p><strong>Platform Fee:</strong> €{transaction['commission']:.2f}</p>
                <p><strong>Total Refund:</strong> €{refund_amount:.2f}</p>
                <p>The refund will be processed within 7 business days.</p>
                """
            )
            try:
                sg.send(message)
            except Exception as e:
                logging.error(f"Failed to send refund email: {str(e)}")
    
    return {
        "success": True,
        "message": "Transaction cancelled and refund initiated",
        "refund_amount": refund_amount
    }

# ============================================
# INVOICE & TRANSACTION HISTORY ENDPOINTS
# ============================================

@app.get("/api/invoices")
async def get_user_invoices(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    transaction_type: Optional[str] = None,  # buyer, seller
    current_user: User = Depends(get_current_user)
):
    """Get all invoices for the current user with filtering options"""
    query = {
        "$or": [
            {"buyer_id": current_user.id},
            {"seller_id": current_user.id}
        ]
    }
    
    # Apply filters
    if status:
        query["invoice_status"] = status
    
    if start_date:
        query["invoice_date"] = {"$gte": start_date}
    
    if end_date:
        if "invoice_date" in query:
            query["invoice_date"]["$lte"] = end_date
        else:
            query["invoice_date"] = {"$lte": end_date}
    
    # Filter by transaction type
    if transaction_type == "buyer":
        query.pop("$or", None)
        query["buyer_id"] = current_user.id
    elif transaction_type == "seller":
        query.pop("$or", None)
        query["seller_id"] = current_user.id
    
    invoices = await db.invoices.find(query).sort("invoice_date", -1).to_list(None)
    
    # Enrich with transaction and product data
    enriched_invoices = []
    for invoice in invoices:
        if "_id" in invoice:
            del invoice["_id"]
        
        # Get product info
        product = await db.products.find_one({"id": invoice["product_id"]})
        transaction = await db.transactions.find_one({"id": invoice["transaction_id"]})
        
        buyer = await db.users.find_one({"id": invoice["buyer_id"]})
        seller = await db.users.find_one({"id": invoice["seller_id"]})
        
        enriched_invoice = {
            **invoice,
            "product_title": product["title"] if product else "Unknown Product",
            "product_images": product.get("images", []) if product else [],
            "buyer_name": buyer["name"] if buyer else "Unknown Buyer",
            "seller_name": seller["name"] if seller else "Unknown Seller",
            "transaction_status": transaction["status"] if transaction else "unknown",
            "user_role": "buyer" if invoice["buyer_id"] == current_user.id else "seller"
        }
        enriched_invoices.append(enriched_invoice)
    
    return enriched_invoices

@app.get("/api/invoices/{invoice_id}")
async def get_invoice_details(
    invoice_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get detailed invoice information"""
    invoice = await db.invoices.find_one({"id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Check authorization
    if invoice["buyer_id"] != current_user.id and invoice["seller_id"] != current_user.id:
        # Check if user is admin
        user = await db.users.find_one({"id": current_user.id})
        if not user or not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Not authorized to view this invoice")
    
    if "_id" in invoice:
        del invoice["_id"]
    
    # Enrich with related data
    transaction = await db.transactions.find_one({"id": invoice["transaction_id"]})
    product = await db.products.find_one({"id": invoice["product_id"]})
    buyer = await db.users.find_one({"id": invoice["buyer_id"]})
    seller = await db.users.find_one({"id": invoice["seller_id"]})
    
    return {
        **invoice,
        "transaction": transaction,
        "product": product,
        "buyer": {
            "name": buyer["name"] if buyer else "Unknown",
            "email": buyer["email"] if buyer else "Unknown",
            "phone": buyer.get("phone", "N/A") if buyer else "N/A"
        },
        "seller": {
            "name": seller["name"] if seller else "Unknown",
            "email": seller["email"] if seller else "Unknown",
            "phone": seller.get("phone", "N/A") if seller else "N/A"
        }
    }

@app.get("/api/invoices/{invoice_id}/download")
async def download_invoice_pdf(
    invoice_id: str,
    current_user: User = Depends(get_current_user)
):
    """Generate and download invoice PDF"""
    invoice = await db.invoices.find_one({"id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Check authorization
    if invoice["buyer_id"] != current_user.id and invoice["seller_id"] != current_user.id:
        # Check if user is admin
        user = await db.users.find_one({"id": current_user.id})
        if not user or not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Not authorized to download this invoice")
    
    # Generate PDF if not exists
    if not invoice.get("pdf_url"):
        pdf_url = await generate_invoice_pdf(invoice_id)
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$set": {"pdf_url": pdf_url, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
    else:
        pdf_url = invoice["pdf_url"]
    
    return {
        "pdf_url": pdf_url,
        "invoice_number": invoice["invoice_number"]
    }

@app.get("/api/transaction-history")
async def get_transaction_history(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    role: Optional[str] = None,  # buyer, seller
    current_user: User = Depends(get_current_user)
):
    """Get complete transaction history with all statuses"""
    query = {
        "$or": [
            {"buyer_id": current_user.id},
            {"seller_id": current_user.id}
        ]
    }
    
    # Apply filters
    if status:
        query["status"] = status
    
    if start_date:
        query["created_at"] = {"$gte": start_date}
    
    if end_date:
        if "created_at" in query:
            query["created_at"]["$lte"] = end_date
        else:
            query["created_at"] = {"$lte": end_date}
    
    # Filter by role
    if role == "buyer":
        query.pop("$or", None)
        query["buyer_id"] = current_user.id
    elif role == "seller":
        query.pop("$or", None)
        query["seller_id"] = current_user.id
    
    transactions = await db.transactions.find(query).sort("created_at", -1).to_list(None)
    
    # Enrich with product, user, and invoice data
    enriched_transactions = []
    for transaction in transactions:
        if "_id" in transaction:
            del transaction["_id"]
        
        # Get related data
        product = await db.products.find_one({"id": transaction["product_id"]})
        buyer = await db.users.find_one({"id": transaction["buyer_id"]})
        seller = await db.users.find_one({"id": transaction["seller_id"]})
        invoice = await db.invoices.find_one({"transaction_id": transaction["id"]})
        
        enriched_transaction = {
            **transaction,
            "product_title": product["title"] if product else "Unknown Product",
            "product_images": product.get("images", []) if product else [],
            "product_category": product.get("category", "Unknown") if product else "Unknown",
            "buyer_name": buyer["name"] if buyer else "Unknown Buyer",
            "buyer_email": buyer["email"] if buyer else "Unknown",
            "seller_name": seller["name"] if seller else "Unknown Seller",
            "seller_email": seller["email"] if seller else "Unknown",
            "user_role": "buyer" if transaction["buyer_id"] == current_user.id else "seller",
            "has_invoice": invoice is not None,
            "invoice_id": invoice["id"] if invoice else None,
            "invoice_number": invoice["invoice_number"] if invoice else None
        }
        enriched_transactions.append(enriched_transaction)
    
    return enriched_transactions

@app.get("/api/admin/invoices")
async def get_all_invoices_admin(
    current_user: User = Depends(get_current_user)
):
    """Get all invoices (admin only)"""
    # Check if user is admin
    user = await db.users.find_one({"id": current_user.id})
    if not user or not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    invoices = await db.invoices.find({}).sort("invoice_date", -1).to_list(None)
    
    # Enrich with data
    enriched_invoices = []
    for invoice in invoices:
        if "_id" in invoice:
            del invoice["_id"]
        
        product = await db.products.find_one({"id": invoice["product_id"]})
        buyer = await db.users.find_one({"id": invoice["buyer_id"]})
        seller = await db.users.find_one({"id": invoice["seller_id"]})
        
        enriched_invoices.append({
            **invoice,
            "product_title": product["title"] if product else "Unknown",
            "buyer_name": buyer["name"] if buyer else "Unknown",
            "seller_name": seller["name"] if seller else "Unknown"
        })
    
    return enriched_invoices

# User Profile Management
@app.get("/api/users/me")
async def get_my_profile(current_user = Depends(get_current_user)):
    """Get current user profile"""
    try:
        # Handle both User object and dict
        user_id = current_user.id if hasattr(current_user, 'id') else current_user["id"]
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found: {user_id}")
        
        if "_id" in user:
            del user["_id"]
        if "password" in user:
            del user["password"]
        
        return user
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.put("/api/users/me")
async def update_my_profile(
    update_data: UserUpdate,
    current_user = Depends(get_current_user)
):
    """Update current user profile"""
    update_dict = {}
    if update_data.name is not None:
        update_dict["name"] = update_data.name
    if update_data.phone is not None:
        update_dict["phone"] = update_data.phone
    
    if not update_dict:
        raise HTTPException(status_code=400, detail="No update data provided")
    
    # Handle both User object and dict
    user_id = current_user.id if hasattr(current_user, 'id') else current_user["id"]
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": update_dict}
    )
    
    user = await db.users.find_one({"id": user_id})
    if "_id" in user:
        del user["_id"]
    if "password" in user:
        del user["password"]
    
    return user

@app.delete("/api/users/me")
async def delete_my_account(current_user = Depends(get_current_user)):
    """Delete current user account and all associated data"""
    user_id = current_user.id if hasattr(current_user, 'id') else current_user["id"]
    
    try:
        # Delete user's products
        await db.products.delete_many({"seller_id": user_id})
        
        # Delete user's transactions
        await db.transactions.delete_many({
            "$or": [
                {"buyer_id": user_id},
                {"seller_id": user_id}
            ]
        })
        
        # Delete user's messages
        await db.messages.delete_many({
            "$or": [
                {"sender_id": user_id},
                {"recipient_id": user_id}
            ]
        })
        
        # Delete user's conversations
        await db.conversations.delete_many({
            "participants": user_id
        })
        
        # Delete user's notifications
        await db.notifications.delete_many({"user_id": user_id})
        
        # Delete user's wishlist
        await db.wishlist.delete_many({"user_id": user_id})
        
        # Delete user's invoices
        await db.invoices.delete_many({
            "$or": [
                {"buyer_id": user_id},
                {"seller_id": user_id}
            ]
        })
        
        # Delete user's reports
        await db.reports.delete_many({
            "$or": [
                {"reporter_id": user_id},
                {"reported_user_id": user_id}
            ]
        })
        
        # Finally, delete the user
        result = await db.users.delete_one({"id": user_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "success": True,
            "message": "Account and all associated data deleted successfully"
        }
    except Exception as e:
        logging.error(f"Error deleting account: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete account")

@app.get("/api/users/me/products")
async def get_user_products(current_user: User = Depends(get_current_user)):
    """Get all products listed by current user"""
    products = await db.products.find({"seller_id": current_user.id}).to_list(None)
    
    # Remove MongoDB _id field
    for product in products:
        if "_id" in product:
            del product["_id"]
    
    return products

@app.delete("/api/products/{product_id}")
async def delete_product(product_id: str, current_user: User = Depends(get_current_user)):
    """Delete a product (only if owned by current user)"""
    product = await db.products.find_one({"id": product_id})
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if product["seller_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this product")
    
    # Check if product has active transactions
    active_transaction = await db.transactions.find_one({
        "product_id": product_id,
        "status": {"$in": ["pending", "held"]}
    })
    
    if active_transaction:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete product with active transactions"
        )
    
    await db.products.delete_one({"id": product_id})
    
    return {"success": True, "message": "Product deleted successfully"}

@app.on_event("startup")
async def startup_event():
    logging.info("Application starting up...")
    
    # Create default admin user if not exists
    admin_email = "admin@relivv.nl"
    existing_admin = await db.users.find_one({"email": admin_email})
    
    if not existing_admin:
        admin_password = get_password_hash("Admin@123")
        admin_user = {
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "password": admin_password,
            "name": "Admin",
            "phone": "+31600000000",
            "is_admin": True,
            "is_verified": True,
            "is_business_seller": False,
            "is_banned": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.users.insert_one(admin_user)
        logging.info(f"Default admin user created: {admin_email} / Admin@123")

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "Relivv Marketplace", "debug": "MODIFIED"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
