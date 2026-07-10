import os
from datetime import datetime, timedelta, timezone
from collections import Counter
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from typing import Optional, List

def get_supabase() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "change-me")

def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return True


# ── Product Schemas ────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: int                        # paise
    images: List[str] = []           # ordered list of image URLs
    image_url: Optional[str] = None  # first image, kept for backwards compat
    active: bool = True

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[int] = None
    images: Optional[List[str]] = None
    image_url: Optional[str] = None
    active: Optional[bool] = None


# ── Category Schemas ───────────────────────────────────────────────────────────

class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    slug: str
    image_url: Optional[str] = None
    active: bool = True

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    image_url: Optional[str] = None
    active: Optional[bool] = None


# ── Site Assets Schemas ────────────────────────────────────────────────────────

VALID_ASSET_KEYS = {"logo", "banner_1", "banner_2", "banner_3"}

class SiteAssetUpsert(BaseModel):
    url: str
    alt: Optional[str] = None
    active: bool = True
    link_url: Optional[str] = None


# ── Review Video Schemas ───────────────────────────────────────────────────────

class ReviewVideoCreate(BaseModel):
    video_url: str
    thumbnail_url: Optional[str] = None
    reviewer_name: str
    reviewer_handle: Optional[str] = None
    caption: Optional[str] = None
    product_id: Optional[str] = None
    sort_order: int = 0
    active: bool = True

class ReviewVideoUpdate(BaseModel):
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    reviewer_name: Optional[str] = None
    reviewer_handle: Optional[str] = None
    caption: Optional[str] = None
    product_id: Optional[str] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


# ── Blog Schemas ───────────────────────────────────────────────────────────────

class BlogPostCreate(BaseModel):
    slug: str
    title: str
    excerpt: Optional[str] = None
    content: Optional[str] = None    # HTML body
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


# ── Router ─────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Product Routes ─────────────────────────────────────────────────────────────

@router.get("/products", dependencies=[Depends(verify_admin)])
def list_products(supabase: Client = Depends(get_supabase)):
    result = supabase.table("products").select("*").order("created_at", desc=True).execute()
    return {"products": result.data}

@router.post("/products", dependencies=[Depends(verify_admin)], status_code=201)
def create_product(body: ProductCreate, supabase: Client = Depends(get_supabase)):
    image_url = body.images[0] if body.images else body.image_url
    result = supabase.table("products").insert({
        "name":        body.name,
        "description": body.description,
        "price":       body.price,
        "images":      body.images,
        "image_url":   image_url,
        "active":      body.active,
    }).execute()
    return {"product": result.data[0]}

@router.patch("/products/{product_id}", dependencies=[Depends(verify_admin)])
def update_product(product_id: str, body: ProductUpdate, supabase: Client = Depends(get_supabase)):
    updates = body.model_dump(exclude_none=True)
    if "images" in updates:
        updates["image_url"] = updates["images"][0] if updates["images"] else None
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = supabase.table("products").update(updates).eq("id", product_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"product": result.data[0]}

@router.delete("/products/{product_id}", dependencies=[Depends(verify_admin)])
def delete_product(product_id: str, supabase: Client = Depends(get_supabase)):
    result = supabase.table("products").delete().eq("id", product_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"deleted": True, "id": product_id}


# ── Category Routes ────────────────────────────────────────────────────────────

@router.get("/categories", dependencies=[Depends(verify_admin)])
def admin_list_categories(supabase: Client = Depends(get_supabase)):
    result = supabase.table("categories").select("*").order("name").execute()
    return {"categories": result.data}

@router.post("/categories", dependencies=[Depends(verify_admin)], status_code=201)
def create_category(body: CategoryCreate, supabase: Client = Depends(get_supabase)):
    existing = supabase.table("categories").select("id").eq("slug", body.slug).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail=f"Category slug '{body.slug}' already exists")
    result = supabase.table("categories").insert({
        "name":        body.name,
        "description": body.description,
        "slug":        body.slug,
        "image_url":   body.image_url,
        "active":      body.active,
    }).execute()
    return {"category": result.data[0]}

@router.patch("/categories/{category_id}", dependencies=[Depends(verify_admin)])
def update_category(category_id: str, body: CategoryUpdate, supabase: Client = Depends(get_supabase)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "slug" in updates:
        existing = supabase.table("categories").select("id").eq("slug", updates["slug"]).execute()
        if existing.data and existing.data[0]["id"] != category_id:
            raise HTTPException(status_code=409, detail=f"Slug '{updates['slug']}' already in use")
    result = supabase.table("categories").update(updates).eq("id", category_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"category": result.data[0]}

@router.delete("/categories/{category_id}", dependencies=[Depends(verify_admin)])
def delete_category(category_id: str, supabase: Client = Depends(get_supabase)):
    result = supabase.table("categories").delete().eq("id", category_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"deleted": True, "id": category_id}


# ── Site Assets: Logo + Banners ────────────────────────────────────────────────

@router.get("/site-assets", dependencies=[Depends(verify_admin)])
def get_all_site_assets(supabase: Client = Depends(get_supabase)):
    result = supabase.table("site_assets").select("*").execute()
    by_key = {row["key"]: row for row in result.data}
    return {"assets": {k: by_key.get(k) for k in VALID_ASSET_KEYS}}

@router.put("/site-assets/{key}", dependencies=[Depends(verify_admin)])
def upsert_site_asset(key: str, body: SiteAssetUpsert, supabase: Client = Depends(get_supabase)):
    if key not in VALID_ASSET_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid key '{key}'. Allowed: {', '.join(sorted(VALID_ASSET_KEYS))}"
        )
    payload = {
        "key":      key,
        "url":      body.url,
        "alt":      body.alt or key.replace("_", " ").title(),
        "active":   body.active,
        "link_url": body.link_url,
    }
    result = supabase.table("site_assets").upsert(payload, on_conflict="key").execute()
    return {"asset": result.data[0]}

@router.patch("/site-assets/{key}/toggle", dependencies=[Depends(verify_admin)])
def toggle_site_asset_active(key: str, supabase: Client = Depends(get_supabase)):
    if key not in VALID_ASSET_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid key '{key}'")
    row = supabase.table("site_assets").select("active").eq("key", key).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail=f"Asset '{key}' not configured yet")
    result = supabase.table("site_assets").update({"active": not row.data[0]["active"]}).eq("key", key).execute()
    return {"asset": result.data[0]}

@router.delete("/site-assets/{key}", dependencies=[Depends(verify_admin)])
def delete_site_asset(key: str, supabase: Client = Depends(get_supabase)):
    if key not in VALID_ASSET_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid key '{key}'")
    supabase.table("site_assets").delete().eq("key", key).execute()
    return {"deleted": True, "key": key}


# ── Review Video Routes ────────────────────────────────────────────────────────

@router.get("/review-videos", dependencies=[Depends(verify_admin)])
def list_review_videos(supabase: Client = Depends(get_supabase)):
    result = (
        supabase.table("review_videos")
        .select("*")
        .order("sort_order")
        .order("created_at", desc=True)
        .execute()
    )
    return {"review_videos": result.data}

@router.post("/review-videos", dependencies=[Depends(verify_admin)], status_code=201)
def create_review_video(body: ReviewVideoCreate, supabase: Client = Depends(get_supabase)):
    result = supabase.table("review_videos").insert({
        "video_url":       body.video_url,
        "thumbnail_url":   body.thumbnail_url,
        "reviewer_name":   body.reviewer_name,
        "reviewer_handle": body.reviewer_handle,
        "caption":         body.caption,
        "product_id":      body.product_id,
        "sort_order":      body.sort_order,
        "active":          body.active,
    }).execute()
    return {"review_video": result.data[0]}

@router.patch("/review-videos/{video_id}", dependencies=[Depends(verify_admin)])
def update_review_video(video_id: str, body: ReviewVideoUpdate, supabase: Client = Depends(get_supabase)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = supabase.table("review_videos").update(updates).eq("id", video_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Review video not found")
    return {"review_video": result.data[0]}

@router.patch("/review-videos/{video_id}/toggle", dependencies=[Depends(verify_admin)])
def toggle_review_video_active(video_id: str, supabase: Client = Depends(get_supabase)):
    row = supabase.table("review_videos").select("active").eq("id", video_id).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Review video not found")
    result = (
        supabase.table("review_videos")
        .update({"active": not row.data[0]["active"]})
        .eq("id", video_id)
        .execute()
    )
    return {"review_video": result.data[0]}

@router.delete("/review-videos/{video_id}", dependencies=[Depends(verify_admin)])
def delete_review_video(video_id: str, supabase: Client = Depends(get_supabase)):
    result = supabase.table("review_videos").delete().eq("id", video_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Review video not found")
    return {"deleted": True, "id": video_id}

@router.patch("/review-videos/reorder", dependencies=[Depends(verify_admin)])
def reorder_review_videos(
    order: List[dict],
    supabase: Client = Depends(get_supabase),
):
    """Bulk-update sort_order for drag-and-drop reordering."""
    for item in order:
        if "id" not in item or "sort_order" not in item:
            raise HTTPException(status_code=400, detail="Each item must have 'id' and 'sort_order'")
        supabase.table("review_videos").update({"sort_order": item["sort_order"]}).eq("id", item["id"]).execute()
    return {"reordered": True, "count": len(order)}


# ── Blog Admin Routes ──────────────────────────────────────────────────────────

@router.get("/blog", dependencies=[Depends(verify_admin)])
def admin_list_blog_posts(
    supabase: Client = Depends(get_supabase),
    active: Optional[bool] = Query(None, description="Filter by active status")
):
    """List all blog posts for admin panel."""
    try:
        query = (
            supabase.table("blog_posts")
            .select("id,slug,title,category,active,published_at,read_time,author")
            .order("published_at", desc=True)
        )
        if active is not None:
            query = query.eq("active", active)
        result = query.execute()
        return {"posts": result.data}
    except Exception:
        return {"posts": []}

@router.post("/blog", dependencies=[Depends(verify_admin)], status_code=201)
def admin_create_blog_post(body: BlogPostCreate, supabase: Client = Depends(get_supabase)):
    """Create a new blog post."""
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

@router.patch("/blog/{slug}", dependencies=[Depends(verify_admin)])
def admin_update_blog_post(slug: str, body: BlogPostUpdate, supabase: Client = Depends(get_supabase)):
    """Partially update a blog post by slug."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = supabase.table("blog_posts").update(updates).eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"post": result.data[0]}

@router.delete("/blog/{slug}", dependencies=[Depends(verify_admin)])
def admin_delete_blog_post(slug: str, supabase: Client = Depends(get_supabase)):
    """Soft-delete a blog post."""
    result = supabase.table("blog_posts").update({"active": False}).eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"success": True, "slug": slug}


# ── Subscription Admin Routes ──────────────────────────────────────────────────

@router.get("/subscriptions", dependencies=[Depends(verify_admin)])
def admin_list_subscriptions(
    supabase: Client = Depends(get_supabase),
    status: Optional[str] = Query(None, description="active | paused | cancelled")
):
    """List all subscriptions, optionally filtered by status."""
    try:
        query = (
            supabase.table("subscriptions")
            .select("*")
            .order("created_at", desc=True)
        )
        if status:
            query = query.eq("status", status)
        result = query.execute()
        return {"subscriptions": result.data, "count": len(result.data)}
    except Exception:
        return {"subscriptions": [], "count": 0}

@router.patch("/subscriptions/{subscription_id}", dependencies=[Depends(verify_admin)])
def admin_update_subscription(
    subscription_id: str,
    status: str = Query(..., description="active | paused | cancelled"),
    supabase: Client = Depends(get_supabase),
):
    """Update subscription status from admin panel."""
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
    return {"subscription": result.data[0]}


# ── Newsletter Admin Routes ────────────────────────────────────────────────────

@router.get("/newsletter-subscribers", dependencies=[Depends(verify_admin)])
def admin_list_newsletter_subscribers(supabase: Client = Depends(get_supabase)):
    """List all newsletter subscribers."""
    try:
        result = (
            supabase.table("newsletter_subscribers")
            .select("*")
            .order("subscribed_at", desc=True)
            .execute()
        )
        return {"subscribers": result.data, "count": len(result.data)}
    except Exception:
        return {"subscribers": [], "count": 0}

@router.delete("/newsletter-subscribers/{email}", dependencies=[Depends(verify_admin)])
def admin_delete_newsletter_subscriber(email: str, supabase: Client = Depends(get_supabase)):
    """Unsubscribe an email from the newsletter."""
    supabase.table("newsletter_subscribers").delete().eq("email", email).execute()
    return {"deleted": True, "email": email}


# ── Purchase / Order Routes ────────────────────────────────────────────────────

@router.get("/purchases", dependencies=[Depends(verify_admin)])
def list_purchases(
    supabase: Client = Depends(get_supabase),
    status: Optional[str] = Query(None, description="paid | pending | failed"),
    payment_method: Optional[str] = Query(None, description="upi | cod"),
    limit: int = Query(100, le=500),
):
    """List all purchases, optionally filtered by payment status and/or payment method."""
    query = (
        supabase.table("purchases")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status:
        query = query.eq("status", status)
    if payment_method:
        query = query.eq("payment_method", payment_method)
    result = query.execute()
    return {"purchases": result.data}

@router.get("/purchases/{purchase_id}", dependencies=[Depends(verify_admin)])
def get_purchase(purchase_id: str, supabase: Client = Depends(get_supabase)):
    result = supabase.table("purchases").select("*").eq("id", purchase_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Purchase not found")
    return {"purchase": result.data[0]}

@router.patch("/purchases/{purchase_id}/status", dependencies=[Depends(verify_admin)])
def admin_update_purchase_status(
    purchase_id: str,
    status: str = Query(..., description="paid | pending | failed | refunded"),
    supabase: Client = Depends(get_supabase),
):
    """Manually update a purchase status (e.g. mark as refunded)."""
    if status not in ("paid", "pending", "failed", "refunded"):
        raise HTTPException(status_code=400, detail="Invalid status")
    result = supabase.table("purchases").update({"status": status}).eq("id", purchase_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Purchase not found")
    return {"purchase": result.data[0]}

# ── Skin Leads Admin Routes ────────────────────────────────────────────────────

@router.get("/skin-leads", dependencies=[Depends(verify_admin)])
def admin_list_skin_leads(
    supabase: Client = Depends(get_supabase),
    saved_only: bool = Query(False, description="If true, only return leads with email attached"),
    limit: int = Query(100, le=500),
):
    """
    List all skin check leads.
    - saved_only=true  → only leads where user provided their email
    - saved_only=false → all entries incl. anonymous tries
    """
    try:
        query = (
            supabase.table("skin_leads")
            .select("id,session_token,email,name,original_url,after_image_url,analysis_text,saved,created_at")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if saved_only:
            query = query.eq("saved", True)
        result = query.execute()
        return {
            "leads": result.data,
            "count": len(result.data),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/skin-leads/{lead_id}", dependencies=[Depends(verify_admin)])
def admin_delete_skin_lead(lead_id: str, supabase: Client = Depends(get_supabase)):
    """Delete a skin lead record (and associated images should be cleaned from storage separately)."""
    result = supabase.table("skin_leads").delete().eq("id", lead_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Skin lead not found")
    return {"deleted": True, "id": lead_id}


# ── Analytics ──────────────────────────────────────────────────────────────────
# Reads from the `events` table populated by the public POST /track endpoint
# in main.py. Everything is aggregated in Python (fine at this table size);
# if traffic grows a lot, move this to a Postgres view / RPC instead.

EVENTS_FETCH_LIMIT = 20000  # safety cap per query so a huge range can't hang the request

def _classify_device(ua: str):
    """Cheap keyword-based UA classification — no external dependency needed."""
    if not ua:
        return "Unknown", "Unknown"
    u = ua.lower()
    if "ipad" in u:
        return "Tablet", "iOS"
    if "iphone" in u:
        return "Mobile", "iOS"
    if "android" in u:
        return ("Mobile" if "mobile" in u else "Tablet"), "Android"
    if "windows" in u:
        return "Desktop", "Windows"
    if "macintosh" in u or "mac os" in u:
        return "Desktop", "macOS"
    if "linux" in u:
        return "Desktop", "Linux"
    return "Unknown", "Unknown"


def _traffic_source(meta: dict, referrer_header: str = None):
    """Best-effort traffic source: UTM source wins, else the referrer's domain, else 'direct'."""
    meta = meta or {}
    if meta.get("utm_source"):
        src = meta["utm_source"]
        if meta.get("utm_medium"):
            src = f"{src} / {meta['utm_medium']}"
        return src
    ref = meta.get("referrer") or referrer_header or ""
    if not ref:
        return "Direct / None"
    try:
        from urllib.parse import urlparse
        host = urlparse(ref).netloc.replace("www.", "")
        return host or "Direct / None"
    except Exception:
        return "Direct / None"


CHECKOUT_STEP_ORDER = ["address_submitted", "payment_step_viewed", "screenshot_uploaded", "submit_clicked", "submit_failed"]

@router.get("/analytics/summary", dependencies=[Depends(verify_admin)])
def analytics_summary(
    days: int = Query(7, ge=1, le=365, description="How many days back to summarise"),
    supabase: Client = Depends(get_supabase),
):
    """
    Aggregated analytics for the admin dashboard: visitor/session counts,
    a pageview → product view → add-to-cart → checkout → purchase funnel,
    top pages, top clicked elements, traffic sources, device mix, checkout
    step drop-off, WhatsApp click breakdown, scroll depth, and a daily
    pageview series.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    empty_shape = {
        "range_days": days, "truncated": False,
        "funnel": {"visitors": 0, "sessions": 0, "pageviews": 0, "product_views": 0,
                   "add_to_cart": 0, "checkout_start": 0, "purchases": 0},
        "top_pages": [], "top_clicks": [], "daily_pageviews": [],
        "traffic_sources": [], "device_breakdown": [], "checkout_funnel": [],
        "whatsapp_clicks": [], "scroll_depth": [],
    }
    try:
        result = (
            supabase.table("events")
            .select("event_type,page,label,visitor_id,session_id,meta,referrer,user_agent,created_at")
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(EVENTS_FETCH_LIMIT)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        # Table probably doesn't exist yet — return empty shape instead of a 500
        # so the dashboard can render a friendly "no data yet" state.
        return {**empty_shape, "error": str(e)}

    def count(etype):
        return sum(1 for r in rows if r.get("event_type") == etype)

    pageviews = [r for r in rows if r.get("event_type") == "pageview"]
    visitor_ids = {r["visitor_id"] for r in rows if r.get("visitor_id")}
    session_ids = {r["session_id"] for r in rows if r.get("session_id")}

    page_counter = Counter(r["page"] for r in pageviews if r.get("page"))
    click_counter = Counter(r["label"] for r in rows if r.get("event_type") == "click" and r.get("label"))
    whatsapp_counter = Counter(
        r["label"] for r in rows
        if r.get("event_type") == "click" and (r.get("label") or "").startswith("whatsapp")
    )

    purchase_rows = [r for r in rows if r.get("event_type") == "purchase"]
    revenue_paise = sum((r.get("meta") or {}).get("amount", 0) or 0 for r in purchase_rows)

    daily = Counter()
    for r in pageviews:
        created = r.get("created_at") or ""
        if len(created) >= 10:
            daily[created[:10]] += 1

    # Traffic sources — derived from each pageview's UTM params / referrer
    source_counter = Counter(_traffic_source(r.get("meta"), r.get("referrer")) for r in pageviews)

    # Device / OS mix — one classification per session, from that session's first-seen user_agent
    session_ua = {}
    for r in reversed(rows):  # rows are desc; reverse to get first-seen (earliest) per session
        sid = r.get("session_id")
        if sid and sid not in session_ua and r.get("user_agent"):
            session_ua[sid] = r["user_agent"]
    device_counter = Counter()
    for ua in session_ua.values():
        device, os_name = _classify_device(ua)
        device_counter[f"{device} ({os_name})"] += 1

    # Checkout step drop-off — ordered so the funnel reads top-to-bottom
    step_counts = Counter(r["label"] for r in rows if r.get("event_type") == "checkout_step" and r.get("label"))
    checkout_funnel = [(step, step_counts.get(step, 0)) for step in CHECKOUT_STEP_ORDER]

    # Scroll depth on product pages — % of product-page visits reaching each milestone
    scroll_counter = Counter(r["label"] for r in rows if r.get("event_type") == "scroll_depth" and r.get("label"))
    scroll_depth = [(m, scroll_counter.get(m, 0)) for m in ["25%", "50%", "75%", "100%"]]

    funnel = {
        "visitors":       len(visitor_ids),
        "sessions":       len(session_ids),
        "pageviews":      len(pageviews),
        "product_views":  count("product_view"),
        "add_to_cart":    count("add_to_cart"),
        "checkout_start": count("checkout_start"),
        "purchases":      len(purchase_rows),
        "revenue_paise":  revenue_paise,
    }

    return {
        "range_days":       days,
        "truncated":        len(rows) >= EVENTS_FETCH_LIMIT,
        "funnel":           funnel,
        "top_pages":        page_counter.most_common(10),
        "top_clicks":       click_counter.most_common(15),
        "daily_pageviews":  sorted(daily.items()),
        "traffic_sources":  source_counter.most_common(10),
        "device_breakdown": device_counter.most_common(10),
        "checkout_funnel":  checkout_funnel,
        "whatsapp_clicks":  whatsapp_counter.most_common(10),
        "scroll_depth":     scroll_depth,
    }


@router.get("/analytics/events", dependencies=[Depends(verify_admin)])
def analytics_raw_events(
    event_type: Optional[str] = Query(None, description="Filter to a single event_type"),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(200, le=1000),
    supabase: Client = Depends(get_supabase),
):
    """Raw event log — useful for debugging or drilling into a specific event type."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        query = (
            supabase.table("events")
            .select("id,event_type,page,label,visitor_id,session_id,meta,referrer,created_at")
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if event_type:
            query = query.eq("event_type", event_type)
        result = query.execute()
        return {"events": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/visitors", dependencies=[Depends(verify_admin)])
def analytics_visitors(
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(50, le=200),
    supabase: Client = Depends(get_supabase),
):
    """
    One row per SESSION (a single visit — session_id resets each new tab/browser
    session), built by grouping raw events. Sorted most-recent-first. Flags
    whether the underlying visitor_id shows up in more than one session in
    this window, so repeat visitors are visible at a glance.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        result = (
            supabase.table("events")
            .select("event_type,page,visitor_id,session_id,meta,ip_address,created_at")
            .gte("created_at", since)
            .order("created_at", desc=False)   # ascending so "first seen" / "entry page" is set correctly
            .limit(EVENTS_FETCH_LIMIT)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        return {"visitors": [], "total_sessions": 0, "truncated": False, "error": str(e)}

    sessions = {}
    for r in rows:
        sid = r.get("session_id")
        if not sid:
            continue
        s = sessions.get(sid)
        if s is None:
            s = sessions[sid] = {
                "session_id": sid,
                "visitor_id": r.get("visitor_id"),
                "ip_address": r.get("ip_address"),
                "first_seen": r.get("created_at"),
                "last_seen": r.get("created_at"),
                "event_count": 0,
                "pages": set(),
                "entry_page": r.get("page"),
                "converted": False,
                "purchase_amount_paise": 0,
            }
        s["event_count"] += 1
        s["last_seen"] = r.get("created_at")  # rows are ascending, so last write is the latest
        if r.get("ip_address") and not s.get("ip_address"):
            s["ip_address"] = r["ip_address"]
        if r.get("page"):
            s["pages"].add(r["page"])
        if r.get("event_type") == "purchase":
            s["converted"] = True
            s["purchase_amount_paise"] += (r.get("meta") or {}).get("amount", 0) or 0

    visitor_session_count = Counter(s["visitor_id"] for s in sessions.values() if s.get("visitor_id"))

    out = [{
        "session_id":            s["session_id"],
        "visitor_id":            s["visitor_id"],
        "ip_address":            s["ip_address"],
        "first_seen":            s["first_seen"],
        "last_seen":             s["last_seen"],
        "event_count":           s["event_count"],
        "page_count":            len(s["pages"]),
        "entry_page":            s["entry_page"],
        "converted":             s["converted"],
        "purchase_amount_paise": s["purchase_amount_paise"],
        "is_repeat_visitor":     visitor_session_count.get(s["visitor_id"], 0) > 1,
    } for s in sessions.values()]

    out.sort(key=lambda x: x["last_seen"] or "", reverse=True)

    return {
        "visitors": out[:limit],
        "total_sessions": len(out),
        "truncated": len(rows) >= EVENTS_FETCH_LIMIT,
    }


@router.get("/analytics/visitor/{session_id}", dependencies=[Depends(verify_admin)])
def analytics_visitor_journey(session_id: str, supabase: Client = Depends(get_supabase)):
    """Full, time-ordered event timeline for a single session — the actual click-by-click journey."""
    try:
        result = (
            supabase.table("events")
            .select("event_type,page,label,visitor_id,ip_address,meta,referrer,created_at")
            .eq("session_id", session_id)
            .order("created_at", desc=False)
            .limit(1000)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        raise HTTPException(status_code=404, detail="No events found for this session")

    visitor_id = next((r.get("visitor_id") for r in rows if r.get("visitor_id")), None)
    ip_address = next((r.get("ip_address") for r in rows if r.get("ip_address")), None)
    return {"session_id": session_id, "visitor_id": visitor_id, "ip_address": ip_address, "events": rows}