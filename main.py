import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("upload")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
import httpx
import uuid
import mimetypes

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from dotenv import load_dotenv
from typing import List, Optional

load_dotenv()

SUPABASE_URL        = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY        = os.environ["SUPABASE_KEY"]
ADMIN_SECRET        = os.environ.get("ADMIN_SECRET", "change-me")
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")
STORAGE_BUCKET      = "product"
REPLICATE_API       = "https://api.replicate.com/v1"

# Replicate model for skin transformation
SKIN_MODEL_VERSION  = "bytedance/seedream-4.5"

# Free shipping threshold in paise (₹199)
FREE_SHIPPING_THRESHOLD = 19900

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Valid upload folder names (kept explicit to prevent path traversal)
VALID_FOLDERS = {"products", "categories", "banners", "logo", "blog"}


# ── Helpers ───────────────────────────────────────────────────────────────────



def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin token")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Prottiva Store API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from admin import router as admin_router
app.include_router(admin_router)

try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass


# ── Image Upload ──────────────────────────────────────────────────────────────

@app.post("/admin/upload-image", dependencies=[Depends(verify_admin)])
async def upload_image(
    file: UploadFile = File(...),
    folder: str = Query(
        default="products",
        description="Storage sub-folder. One of: products, categories, banners, logo, blog"
    ),
):
    """
    Upload any image to Supabase Storage and get back a public URL.

    Use the `folder` query param to keep images organised:
      - ?folder=products    (default) – product images
      - ?folder=categories  – category thumbnail images
      - ?folder=banners     – homepage banner images
      - ?folder=logo        – store logo
      - ?folder=blog        – blog post cover images

    After getting the URL, call the relevant endpoint to save it.
    """
    logger.info(f"[upload] START folder={folder!r} filename={file.filename!r} content_type={file.content_type!r} size_hint={file.size}")

    # ── folder check ──────────────────────────────────────────────────────────
    if folder not in VALID_FOLDERS:
        logger.warning(f"[upload] REJECTED invalid folder={folder!r}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid folder '{folder}'. Must be one of: {', '.join(sorted(VALID_FOLDERS))}"
        )

    # ── content-type check ────────────────────────────────────────────────────
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
    content_type = file.content_type or "application/octet-stream"
    logger.info(f"[upload] content_type resolved to {content_type!r}")
    if content_type not in allowed_types:
        logger.warning(f"[upload] REJECTED unsupported content_type={content_type!r}")
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}")

    # ── read & size check ─────────────────────────────────────────────────────
    data = await file.read()
    size_kb = len(data) / 1024
    logger.info(f"[upload] read {size_kb:.1f} KB")
    if len(data) > 5 * 1024 * 1024:
        logger.warning(f"[upload] REJECTED file too large: {size_kb:.1f} KB")
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    # ── build storage path ────────────────────────────────────────────────────
    ext = mimetypes.guess_extension(content_type) or ".jpg"
    ext = ext.replace(".jpe", ".jpg")
    filename = f"{folder}/{uuid.uuid4().hex}{ext}"
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{filename}"
    logger.info(f"[upload] uploading to {upload_url}")

    # ── push to Supabase Storage ──────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                upload_url,
                content=data,
                headers={
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type":  content_type,
                    "x-upsert":      "true",
                },
            )
    except Exception as exc:
        logger.error(f"[upload] HTTP ERROR reaching Supabase: {exc}")
        raise HTTPException(status_code=502, detail=f"Could not reach Supabase Storage: {exc}")

    logger.info(f"[upload] Supabase responded {resp.status_code}: {resp.text[:300]}")

    if resp.status_code not in (200, 201):
        try:
            detail = resp.json()
            msg = detail.get("message") or detail.get("error") or resp.text
        except Exception:
            msg = resp.text
        logger.error(f"[upload] STORAGE FAILED status={resp.status_code} msg={msg!r}")
        raise HTTPException(
            status_code=500,
            detail=f"Storage upload failed ({resp.status_code}): {msg}"
        )

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{filename}"
    logger.info(f"[upload] SUCCESS public_url={public_url}")
    return {"url": public_url, "filename": filename, "folder": folder}


# ── Public Media Upload (image OR video) ──────────────────────────────────────

PUBLIC_UPLOAD_FOLDER = "public-uploads"  # everything from this endpoint lives here

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska"}

MAX_IMAGE_SIZE = 5 * 1024 * 1024     # 5 MB
MAX_VIDEO_SIZE = 50 * 1024 * 1024    # 50 MB — Railway request body limits may bite above this


@app.post("/upload-media")
async def upload_media(file: UploadFile = File(...)):
    """
    PUBLIC endpoint — no auth required.

    Upload a single image or video file. It is stored in Supabase Storage
    under the "public-uploads/" folder and a public URL is returned.

    Accepted types:
      - Images: jpeg, png, webp, gif, avif   (max 5 MB)
      - Videos: mp4, webm, mov, mkv           (max 50 MB)

    NOTE: Since this endpoint has no auth, anyone with the URL can upload.
    Consider adding rate limiting / CAPTCHA if abuse becomes a problem.
    """
    content_type = file.content_type or "application/octet-stream"
    logger.info(f"[public-upload] START filename={file.filename!r} content_type={content_type!r}")

    if content_type in ALLOWED_IMAGE_TYPES:
        media_kind = "image"
        max_size = MAX_IMAGE_SIZE
    elif content_type in ALLOWED_VIDEO_TYPES:
        media_kind = "video"
        max_size = MAX_VIDEO_SIZE
    else:
        logger.warning(f"[public-upload] REJECTED unsupported content_type={content_type!r}")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {content_type}. Allowed: images (jpeg/png/webp/gif/avif) or videos (mp4/webm/mov/mkv)."
        )

    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    logger.info(f"[public-upload] read {size_mb:.2f} MB, kind={media_kind}")

    if len(data) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f} MB). Max for {media_kind}: {max_size // (1024*1024)} MB"
        )

    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = mimetypes.guess_extension(content_type) or (".mp4" if media_kind == "video" else ".jpg")
    ext = ext.replace(".jpe", ".jpg").replace(".qt", ".mov")
    filename = f"{PUBLIC_UPLOAD_FOLDER}/{media_kind}/{uuid.uuid4().hex}{ext}"
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{filename}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                upload_url,
                content=data,
                headers={
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type":  content_type,
                    "x-upsert":      "true",
                },
            )
    except Exception as exc:
        logger.error(f"[public-upload] HTTP ERROR reaching Supabase: {exc}")
        raise HTTPException(status_code=502, detail=f"Could not reach Supabase Storage: {exc}")

    if resp.status_code not in (200, 201):
        try:
            detail = resp.json()
            msg = detail.get("message") or detail.get("error") or resp.text
        except Exception:
            msg = resp.text
        logger.error(f"[public-upload] STORAGE FAILED status={resp.status_code} msg={msg!r}")
        raise HTTPException(status_code=500, detail=f"Storage upload failed ({resp.status_code}): {msg}")

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{filename}"
    logger.info(f"[public-upload] SUCCESS public_url={public_url}")

    return {
        "url":       public_url,
        "filename":  filename,
        "type":      media_kind,
        "content_type": content_type,
        "size_bytes": len(data),
    }


# ── Direct-to-Supabase video upload config ────────────────────────────────────

@app.get("/admin/video-upload-config", dependencies=[Depends(verify_admin)])
def video_upload_config():
    """
    Returns Supabase credentials so the browser uploads videos DIRECTLY to
    Supabase Storage — bypassing Railway's body-size and timeout limits.
    """
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_key": SUPABASE_KEY,
        "bucket":       STORAGE_BUCKET,
        "folder":       "reviews",
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

class CartItem(BaseModel):
    id: str
    name: str
    qty: int
    price: int               # paise (already discounted if subscription)
    is_subscription: bool = False
    sub_frequency: Optional[str] = None   # "30" | "60" | "90" days

class DeliveryAddress(BaseModel):
    line1: str
    line2: Optional[str] = None
    city: str
    state: str
    pincode: str
    country: str = "India"



class NewsletterRequest(BaseModel):
    email: EmailStr

class SubscriptionRequest(BaseModel):
    product_id: str
    customer_name: str
    customer_email: EmailStr
    customer_phone: str
    delivery_address: DeliveryAddress
    frequency_days: int = 60      # 30 / 60 / 90
    discount_pct: float = 15.0    # 15% off

class BlogPostCreate(BaseModel):
    slug: str
    title: str
    excerpt: Optional[str] = None
    content: Optional[str] = None   # HTML string stored as text
    category: Optional[str] = None  # nutrition | skincare | wellness | guides
    cover_url: Optional[str] = None
    author: Optional[str] = "Prottiva Team"
    published_at: Optional[str] = None
    read_time: Optional[int] = 5
    active: bool = True

class BlogPostUpdate(BaseModel):
    title: Optional[str] = None
    excerpt: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    cover_url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    read_time: Optional[int] = None
    active: Optional[bool] = None

class SkinCheckSaveRequest(BaseModel):
    session_token: str
    email: EmailStr
    name: Optional[str] = None

class AuthRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class AuthLoginRequest(BaseModel):
    email: EmailStr
    password: str

class EventIn(BaseModel):
    """A single analytics event sent from the storefront."""
    event_type: str                       # pageview | product_view | add_to_cart | checkout_start | checkout_step | purchase | click
    page: Optional[str] = None            # e.g. "/", "/product.html"
    label: Optional[str] = None           # button label / product name / step name
    visitor_id: Optional[str] = None      # long-lived anonymous id (localStorage)
    session_id: Optional[str] = None      # per-tab-session id (sessionStorage)
    meta: Optional[dict] = None           # extra structured data (product_id, amount, qty, ...)


# ── Auth Helper ───────────────────────────────────────────────────────────────

def get_current_user(authorization: str = Header(...)):
    """Extract and verify Supabase JWT from Authorization: Bearer <token>"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        # Use Supabase service client to get user from token
        user_resp = supabase.auth.get_user(token)
        if not user_resp or not user_resp.user:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        return user_resp.user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


# ── Public Routes ─────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {"status": "ok", "store": "Prottiva Nutrition", "version": "3.0.0"}


# ── Analytics Tracking (public) ───────────────────────────────────────────────

VALID_EVENT_TYPES = {
    "pageview", "product_view", "add_to_cart", "remove_from_cart",
    "cart_open", "checkout_start", "checkout_step", "purchase", "click",
}

@app.post("/track")
async def track_event(body: EventIn, request: Request):
    """
    PUBLIC endpoint — no auth required. Fire-and-forget analytics ingestion.

    The frontend calls this (via navigator.sendBeacon or fetch) for every
    pageview / click / cart / checkout event. Failures here must NEVER
    break the storefront, so all errors are swallowed.

    Requires an `events` table in Supabase (see README / migrations).
    """
    event_type = (body.event_type or "").strip().lower()
    if event_type not in VALID_EVENT_TYPES:
        # Don't hard-fail on unknown event types — just ignore silently so
        # a typo in the frontend never surfaces as a console error.
        return {"ok": True, "recorded": False}

    try:
        supabase.table("events").insert({
            "event_type":  event_type,
            "page":        (body.page or "")[:300] or None,
            "label":       (body.label or "")[:300] or None,
            "visitor_id":  body.visitor_id,
            "session_id":  body.session_id,
            "meta":        body.meta or {},
            "referrer":    request.headers.get("referer", "")[:500] or None,
            "user_agent":  request.headers.get("user-agent", "")[:500] or None,
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        return {"ok": True, "recorded": True}
    except Exception as exc:
        logger.warning(f"[track] failed to record event={event_type!r}: {exc}")
        return {"ok": True, "recorded": False}


# ── Site Assets (public) ──────────────────────────────────────────────────────

@app.get("/site-assets")
def public_site_assets():
    """Returns logo + active banners for the storefront."""
    result = supabase.table("site_assets").select("*").eq("active", True).execute()
    logo    = None
    banners = []
    for row in result.data:
        if row["key"] == "logo":
            logo = {"url": row["url"], "alt": row.get("alt", "Store Logo")}
        elif row["key"].startswith("banner_"):
            banners.append({
                "key":      row["key"],
                "url":      row["url"],
                "alt":      row.get("alt", ""),
                "link_url": row.get("link_url"),
            })
    banners.sort(key=lambda b: b["key"])
    return {"logo": logo, "banners": banners}


# ── Categories (public) ───────────────────────────────────────────────────────

@app.get("/categories")
def public_categories():
    """Return all active categories. Returns empty list if table does not exist."""
    try:
        result = (
            supabase.table("categories")
            .select("id,name,description,slug,image_url")
            .eq("active", True)
            .order("name")
            .execute()
        )
        return {"categories": result.data}
    except Exception:
        return {"categories": []}


# ── Products (public) ─────────────────────────────────────────────────────────

@app.get("/products")
def public_products():
    result = (
        supabase.table("products")
        .select("id,name,description,price,image_url,images,created_at")
        .eq("active", True)
        .order("created_at", desc=True)
        .execute()
    )
    return {"products": result.data}

@app.get("/products/{product_id}")
def public_product_by_id(product_id: str):
    """Fetch a single active product by UUID — used by product.html"""
    result = (
        supabase.table("products")
        .select("id,name,description,price,image_url,images,created_at")
        .eq("id", product_id)
        .eq("active", True)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"product": result.data[0]}


# ── Review Videos (public) ────────────────────────────────────────────────────

@app.get("/review-videos")
def public_review_videos(product_id: Optional[str] = Query(None)):
    """Returns all ACTIVE review videos. Optional ?product_id= to filter."""
    query = (
        supabase.table("review_videos")
        .select("id,video_url,thumbnail_url,reviewer_name,reviewer_handle,caption,product_id,sort_order")
        .eq("active", True)
        .order("sort_order")
        .order("created_at", desc=True)
    )
    if product_id:
        query = query.eq("product_id", product_id)
    result = query.execute()
    return {"review_videos": result.data}


# ── Newsletter ────────────────────────────────────────────────────────────────

@app.post("/newsletter")
def subscribe_newsletter(body: NewsletterRequest):
    """
    Subscribe an email address to the newsletter.
    Requires a `newsletter_subscribers` table:
      - email (text, primary key / unique)
      - subscribed_at (timestamptz, default now())
    """
    try:
        supabase.table("newsletter_subscribers").upsert(
            {"email": body.email},
            on_conflict="email"
        ).execute()
    except Exception:
        # Table may not exist yet — silently pass to avoid breaking the frontend
        pass
    return {"success": True, "message": "Subscribed successfully"}


# ── Subscriptions (Subscribe & Save) ─────────────────────────────────────────

@app.post("/subscriptions")
def create_subscription(body: SubscriptionRequest):
    """
    Create a Subscribe & Save record with 15% discount.

    Requires a `subscriptions` table:
      id uuid default gen_random_uuid() primary key,
      product_id uuid references products(id),
      product_name text,
      base_price int,
      discounted_price int,
      discount_pct float,
      customer_name text,
      customer_email text,
      customer_phone text,
      delivery_address jsonb,
      frequency_days int,
      status text default 'active',  -- active | paused | cancelled
      created_at timestamptz default now()
    """
    prod = (
        supabase.table("products")
        .select("id,name,price")
        .eq("id", body.product_id)
        .eq("active", True)
        .execute()
    )
    if not prod.data:
        raise HTTPException(status_code=404, detail="Product not found")

    product = prod.data[0]
    discounted_price = int(product["price"] * (1 - body.discount_pct / 100))

    result = supabase.table("subscriptions").insert({
        "product_id":       body.product_id,
        "product_name":     product["name"],
        "base_price":       product["price"],
        "discounted_price": discounted_price,
        "discount_pct":     body.discount_pct,
        "customer_name":    body.customer_name,
        "customer_email":   body.customer_email,
        "customer_phone":   body.customer_phone,
        "delivery_address": body.delivery_address.model_dump(),
        "frequency_days":   body.frequency_days,
        "status":           "active",
    }).execute()

    return {
        "success":         True,
        "subscription_id": result.data[0]["id"] if result.data else None,
        "discounted_price": discounted_price,
        "message":         f"Subscribed! You save {body.discount_pct:.0f}% on every delivery."
    }

@app.get("/subscriptions")
def get_subscriptions_by_email(email: str = Query(..., description="Customer email")):
    """Get all subscriptions for a customer by email."""
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("customer_email", email)
        .order("created_at", desc=True)
        .execute()
    )
    return {"subscriptions": result.data}

@app.patch("/subscriptions/{subscription_id}/status")
def update_subscription_status(
    subscription_id: str,
    status: str = Query(..., description="active | paused | cancelled"),
):
    """Allow customers to pause or cancel their subscription."""
    if status not in ("active", "paused", "cancelled"):
        raise HTTPException(status_code=400, detail="status must be: active | paused | cancelled")
    result = (
        supabase.table("subscriptions")
        .update({"status": status})
        .eq("id", subscription_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"success": True, "subscription": result.data[0]}


# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.post("/auth/register", status_code=201)
def auth_register(body: AuthRegisterRequest):
    """Register a new user with email + password via Supabase Auth."""
    try:
        resp = supabase.auth.sign_up({
            "email": body.email,
            "password": body.password,
            "options": {"data": {"name": body.name or ""}},
        })
        if not resp.user:
            raise HTTPException(status_code=400, detail="Registration failed. Email may already be in use.")
        return {
            "message": "Account created! Please check your email to confirm your account, then log in.",
            "user_id": resp.user.id,
            "email": resp.user.email,
        }
    except HTTPException:
        raise
    except Exception as e:
        err = str(e)
        if "already registered" in err or "already been registered" in err:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=400, detail=err)


@app.post("/auth/login")
def auth_login(body: AuthLoginRequest):
    """Login with email + password. Returns access_token for subsequent requests."""
    try:
        resp = supabase.auth.sign_in_with_password({
            "email": body.email,
            "password": body.password,
        })
        if not resp.session:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        return {
            "access_token": resp.session.access_token,
            "refresh_token": resp.session.refresh_token,
            "expires_in": resp.session.expires_in,
            "user": {
                "id": resp.user.id,
                "email": resp.user.email,
                "name": (resp.user.user_metadata or {}).get("name", ""),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid email or password.")


@app.post("/auth/logout")
def auth_logout(authorization: str = Header(...)):
    """Logout — invalidates the current session token."""
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    return {"message": "Logged out successfully."}


@app.post("/auth/refresh")
def auth_refresh(body: dict):
    """Refresh an expired access token using refresh_token."""
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    try:
        resp = supabase.auth.refresh_session(refresh_token)
        if not resp.session:
            raise HTTPException(status_code=401, detail="Could not refresh session.")
        return {
            "access_token": resp.session.access_token,
            "refresh_token": resp.session.refresh_token,
            "expires_in": resp.session.expires_in,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Could not refresh session.")


@app.get("/profile/me")
def get_profile_me(user=Depends(get_current_user)):
    """
    Protected endpoint — returns the logged-in user's orders + skin checks.
    Requires: Authorization: Bearer <access_token>
    """
    email = user.email
    try:
        orders_resp = (
            supabase.table("purchases")
            .select("id,razorpay_order_id,razorpay_payment_id,customer_name,customer_email,customer_phone,amount,currency,items,status,delivery_address,created_at")
            .eq("customer_email", email)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        orders_resp = type("R", (), {"data": []})()

    try:
        skin_resp = (
            supabase.table("skin_leads")
            .select("id,session_token,original_url,after_image_url,analysis_text,created_at")
            .eq("email", email)
            .eq("saved", True)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        skin_resp = type("R", (), {"data": []})()

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "name": (user.user_metadata or {}).get("name", ""),
        },
        "orders": orders_resp.data or [],
        "skin_checks": skin_resp.data or [],
    }


# ── Customer Profile & Order History ─────────────────────────────────────────

@app.get("/profile/orders")
def get_order_history(
    email: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
):
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Provide email or phone query param")

    query = supabase.table("purchases").select(
        "id,created_at,razorpay_order_id,razorpay_payment_id,"
        "amount,currency,status,items,delivery_address,customer_name,"
        "customer_email,customer_phone,notes"
    )
    if email:
        query = query.eq("customer_email", email)
    else:
        query = query.eq("customer_phone", phone)

    result = query.order("created_at", desc=True).execute()
    return {"orders": result.data}

@app.get("/profile/orders/{order_id}")
def get_order_detail(order_id: str):
    result = supabase.table("purchases").select("*").eq("id", order_id).execute()
    if not result.data:
        result = supabase.table("purchases").select("*").eq("razorpay_order_id", order_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order": result.data[0]}


# ── Blog (public) ─────────────────────────────────────────────────────────────

@app.get("/blog")
def public_blog_posts(
    category: Optional[str] = Query(None, description="Filter by category slug"),
    limit:    int            = Query(20, le=50, description="Max posts to return"),
):
    """
    Return published blog posts, newest first.
    Requires a `blog_posts` table — silently returns [] if not yet created.

    Table schema:
      id uuid default gen_random_uuid() primary key,
      slug text unique not null,
      title text not null,
      excerpt text,
      content text,          -- HTML body
      category text,         -- nutrition | skincare | wellness | guides
      cover_url text,
      author text default 'Prottiva Team',
      published_at date,
      read_time int default 5,
      active boolean default true,
      created_at timestamptz default now()
    """
    try:
        query = (
            supabase.table("blog_posts")
            .select("id,slug,title,excerpt,category,cover_url,author,published_at,read_time")
            .eq("active", True)
            .order("published_at", desc=True)
            .limit(limit)
        )
        if category:
            query = query.eq("category", category)
        result = query.execute()
        return {"posts": result.data}
    except Exception:
        return {"posts": []}

@app.get("/blog/{slug}")
def public_blog_post(slug: str):
    """Return a single published blog post by slug (full content)."""
    try:
        result = (
            supabase.table("blog_posts")
            .select("*")
            .eq("slug", slug)
            .eq("active", True)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Post not found")
        return {"post": result.data[0]}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Post not found")


# ── Blog Admin ────────────────────────────────────────────────────────────────

@app.get("/admin/blog", dependencies=[Depends(verify_admin)])
def admin_list_blog_posts():
    """List all blog posts (active and inactive) for admin."""
    try:
        result = (
            supabase.table("blog_posts")
            .select("id,slug,title,category,active,published_at,read_time")
            .order("published_at", desc=True)
            .execute()
        )
        return {"posts": result.data}
    except Exception:
        return {"posts": []}

@app.post("/admin/blog", dependencies=[Depends(verify_admin)], status_code=201)
def admin_create_blog_post(body: BlogPostCreate):
    """Create a new blog post."""
    # Check for duplicate slug
    try:
        existing = supabase.table("blog_posts").select("id").eq("slug", body.slug).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already exists")
        result = supabase.table("blog_posts").insert(body.model_dump()).execute()
        return {"post": result.data[0] if result.data else {}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/admin/blog/{slug}", dependencies=[Depends(verify_admin)])
def admin_update_blog_post(slug: str, body: BlogPostUpdate):
    """Partially update a blog post by slug."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = supabase.table("blog_posts").update(updates).eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"post": result.data[0]}

@app.delete("/admin/blog/{slug}", dependencies=[Depends(verify_admin)])
def admin_delete_blog_post(slug: str):
    """Soft-delete a blog post (sets active=False)."""
    result = supabase.table("blog_posts").update({"active": False}).eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"success": True, "slug": slug}


# ── Admin: Subscriptions ──────────────────────────────────────────────────────

@app.get("/admin/subscriptions", dependencies=[Depends(verify_admin)])
def admin_list_subscriptions(
    status: Optional[str] = Query(None, description="active | paused | cancelled")
):
    """List all subscriptions (optionally filtered by status)."""
    try:
        query = supabase.table("subscriptions").select("*").order("created_at", desc=True)
        if status:
            query = query.eq("status", status)
        result = query.execute()
        return {"subscriptions": result.data}
    except Exception:
        return {"subscriptions": []}

@app.get("/admin/newsletter-subscribers", dependencies=[Depends(verify_admin)])
def admin_list_newsletter_subscribers():
    """List all newsletter subscribers."""
    try:
        result = supabase.table("newsletter_subscribers").select("*").order("subscribed_at", desc=True).execute()
        return {"subscribers": result.data, "count": len(result.data)}
    except Exception:
        return {"subscribers": [], "count": 0}


# ── Checkout: Submit Order with UPI screenshot ────────────────────────────────

import json as _json

@app.post("/submit-order")
async def submit_order(
    screenshot: UploadFile = File(...),
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    amount: int = Form(...),
    items: str = Form("[]"),
    delivery_address: str = Form("{}"),
    notes: Optional[str] = Form(None),
):
    """
    Single endpoint for the QR-pay flow:
    1. Validates inputs
    2. Uploads the payment screenshot to Supabase Storage
    3. Inserts a purchase row with status='paid'
    """
    # Basic validation
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid order amount")

    phone = phone.strip()
    if not phone.lstrip("+").isdigit() or len(phone.lstrip("+")) < 10:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    # Validate screenshot
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    content_type = screenshot.content_type or "application/octet-stream"
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Screenshot must be JPG, PNG or WebP")

    ss_data = await screenshot.read()
    if len(ss_data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Screenshot too large (max 5 MB)")

    # Upload screenshot to Supabase Storage under screenshots/
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type, ".jpg")
    ss_filename = f"screenshots/{uuid.uuid4().hex}{ext}"
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{ss_filename}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            upload_url,
            content=ss_data,
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type":  content_type,
                "x-upsert":      "true",
            },
        )

    if resp.status_code not in (200, 201):
        detail = resp.json() if resp.content else {}
        raise HTTPException(status_code=500, detail=f"Screenshot upload failed: {detail.get('message', 'unknown error')}")

    screenshot_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{ss_filename}"

    # Parse JSON fields sent as form strings
    try:
        items_list = _json.loads(items)
    except Exception:
        items_list = []

    try:
        address = _json.loads(delivery_address)
    except Exception:
        address = {}

    # Insert purchase row — status is 'paid' immediately on screenshot upload
    order_id = f"UPI-{uuid.uuid4().hex[:12].upper()}"
    result = supabase.table("purchases").insert({
        "razorpay_order_id":  order_id,      # repurposed as our internal order ref
        "customer_name":      name,
        "customer_email":     email,
        "customer_phone":     phone,
        "delivery_address":   address,
        "amount":             amount,
        "currency":           "INR",
        "items":              items_list,
        "notes":              notes,
        "status":             "paid",
        "screenshot_url":     screenshot_url,
    }).execute()

    order = result.data[0] if result.data else {}

    # Auto-create subscription records for subscription items
    for item in items_list:
        if item.get("is_subscription"):
            try:
                supabase.table("subscriptions").insert({
                    "product_id":       item.get("id"),
                    "product_name":     item.get("name"),
                    "base_price":       item.get("price"),
                    "discounted_price": item.get("price"),
                    "discount_pct":     15.0,
                    "customer_name":    name,
                    "customer_email":   email,
                    "customer_phone":   phone,
                    "delivery_address": address,
                    "frequency_days":   int(item.get("sub_frequency") or 60),
                    "status":           "active",
                    "purchase_id":      order.get("id"),
                }).execute()
            except Exception:
                pass

    return {"success": True, "order_id": order_id, "screenshot_url": screenshot_url}


# ── Admin: List all purchases ─────────────────────────────────────────────────

@app.get("/admin/purchases", dependencies=[Depends(verify_admin)])
def admin_list_purchases():
    """Return all purchase records for the admin dashboard."""
    result = (
        supabase.table("purchases")
        .select("id,razorpay_order_id,customer_name,customer_email,customer_phone,amount,currency,status,items,delivery_address,screenshot_url,created_at")
        .order("created_at", desc=True)
        .execute()
    )
    return {"purchases": result.data or []}

# ── Skin Check (Lead Magnet) ───────────────────────────────────────────────────

async def _poll_replicate(prediction_id: str, client: httpx.AsyncClient, max_wait: int = 120) -> dict:
    """Poll Replicate until prediction completes or times out."""
    import asyncio
    headers = {"Authorization": f"Token {REPLICATE_API_TOKEN}"}
    for _ in range(max_wait // 2):
        await asyncio.sleep(2)
        resp = await client.get(
            f"{REPLICATE_API}/predictions/{prediction_id}",
            headers=headers,
        )
        data = resp.json()
        if data.get("status") in ("succeeded", "failed", "canceled"):
            return data
    raise HTTPException(status_code=504, detail="Skin analysis timed out. Please try again.")


@app.post("/skin-check")
async def skin_check(file: UploadFile = File(...)):
    """
    Lead magnet: analyse skin and generate a glowing 'after' transformation.

    - Accepts a face/skin photo (jpeg/png/webp, max 5 MB).
    - Calls Replicate bytedance/seedream-4.5 to generate an improved skin version.
    - Stores the result anonymously with a session_token.
    - Returns analysis + after_image_url + session_token immediately.
    - Frontend then calls POST /skin-check/save to attach an email.

    Requires env var: REPLICATE_API_TOKEN
    """
    if not REPLICATE_API_TOKEN:
        raise HTTPException(status_code=503, detail="Skin checker not configured (missing REPLICATE_API_TOKEN)")

    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    content_type = file.content_type or "application/octet-stream"
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, or WebP image.")

    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 5 MB)")

    # Upload original image to Supabase Storage first —
    # we need the public URL to pass into Replicate's image_input field.
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type, ".jpg")
    original_filename = f"skin-checks/original/{uuid.uuid4().hex}{ext}"
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{original_filename}"

    original_url = None
    async with httpx.AsyncClient() as client:
        up = await client.post(
            upload_url,
            content=data,
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": content_type,
                "x-upsert": "true",
            },
        )
        if up.status_code in (200, 201):
            original_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{original_filename}"

    if not original_url:
        raise HTTPException(status_code=500, detail="Failed to upload image. Please try again.")

    # Call Replicate seedream-4.5 with the public image URL in image_input.
    # The model does img-to-img generation — it uses the uploaded face as reference
    # and applies the prompt to enhance/transform the skin.
    SKIN_PROMPT = (
        "The same person with slightly healthier and clearer skin. "
        "Minimal, natural-looking improvement — reduce minor blemishes and uneven tone only. "
        "Keep natural pores, skin texture, and all human imperfections intact. "
        "Do NOT over-smooth, over-brighten, or make the skin look airbrushed, waxy, or plastic. "
        "The result must look like a real person, not a filtered or retouched photo. "
        "Same face, same background, same lighting. Photorealistic, believable result."
    )

    replicate_headers = {
        "Authorization": f"Token {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    after_image_url = None

    async with httpx.AsyncClient(timeout=130) as client:
        create_resp = await client.post(
            f"{REPLICATE_API}/models/{SKIN_MODEL_VERSION}/predictions",
            headers=replicate_headers,
            json={
                "input": {
                    "prompt": SKIN_PROMPT,
                    "image_input": [original_url],
                    "aspect_ratio": "match_input_image",
                    "size": "2K",
                    "sequential_image_generation": "disabled",
                }
            },
        )

        if not create_resp.is_success:
            raise HTTPException(
                status_code=502,
                detail=f"Replicate error: {create_resp.text[:200]}"
            )

        prediction = create_resp.json()

        if prediction.get("status") not in ("succeeded", "failed"):
            prediction = await _poll_replicate(prediction["id"], client)

        if prediction.get("status") == "succeeded":
            output = prediction.get("output")
            if isinstance(output, list) and output:
                after_image_url = output[0]
            elif isinstance(output, str):
                after_image_url = output

    if after_image_url:
        analysis_text = (
            "✨ Your skin has visible potential for improvement! "
            "Based on your photo, we can see signs of uneven tone and texture. "
            "With consistent use of our skincare range — formulated with natural actives — "
            "you could achieve noticeably clearer, more radiant skin like shown in your transformation. 🌿"
        )
    else:
        analysis_text = (
            "We analysed your skin and found areas where targeted nutrition and skincare "
            "could make a significant difference — from hydration to tone evenness. "
            "Explore our range crafted to support exactly this."
        )

    session_token = uuid.uuid4().hex
    try:
        supabase.table("skin_leads").insert({
            "session_token":   session_token,
            "original_url":    original_url,
            "after_image_url": after_image_url,
            "analysis_text":   analysis_text,
            "email":           None,
            "name":            None,
            "saved":           False,
        }).execute()
    except Exception:
        pass  # Don't fail if table doesn't exist yet

    return {
        "session_token":   session_token,
        "analysis_text":   analysis_text,
        "after_image_url": after_image_url,
        "original_url":    original_url,
    }


@app.post("/skin-check/save")
def skin_check_save(body: SkinCheckSaveRequest):
    """
    Attach email + name to an existing anonymous skin check session.
    Call this after the user sees results and chooses to save them.
    """
    try:
        result = (
            supabase.table("skin_leads")
            .update({
                "email":  body.email,
                "name":   body.name,
                "saved":  True,
            })
            .eq("session_token", body.session_token)
            .select("*")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found. Please run the skin check again.")

    return {
        "success":    True,
        "skin_check": result.data[0],
        "message":    "Results saved! View them anytime from your profile.",
    }


@app.get("/profile/skin-checks")
def get_skin_checks_by_email(email: str = Query(..., description="Customer email")):
    """
    Fetch all saved skin checks for a customer by email.
    Same pattern as GET /profile/orders.
    """
    try:
        result = (
            supabase.table("skin_leads")
            .select("id,session_token,original_url,after_image_url,analysis_text,created_at")
            .eq("email", email)
            .eq("saved", True)
            .order("created_at", desc=True)
            .execute()
        )
        return {"skin_checks": result.data}
    except Exception:
        return {"skin_checks": []}