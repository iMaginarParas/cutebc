# 🛍️ Ecom — Backend API

> FastAPI backend for Cute Store, deployed on Railway. Handles product catalog, Razorpay payments, and order management with Supabase as the database and file storage.

**Live API:** `https://cutestorebc-production.up.railway.app`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI (Python) |
| Database | Supabase (PostgreSQL) |
| Image Storage | Supabase Storage |
| Payments | Razorpay |
| Hosting | Railway |

---

## Environment Variables

Set these in your Railway project (or `.env` for local dev):

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
RAZORPAY_KEY_ID=rzp_live_XXXXXXXXXX
RAZORPAY_KEY_SECRET=your-razorpay-secret
ADMIN_SECRET=your-admin-token          # protects all /admin/* routes
RAZORPAY_WEBHOOK_SECRET=optional       # for webhook signature verification
```

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server
uvicorn main:app --reload --port 8000
```

API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

---

## API Reference

### Public Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Health check |
| `GET` | `/products` | List all active products |
| `POST` | `/create-order` | Create a Razorpay order |
| `POST` | `/verify-payment` | Verify payment signature |
| `POST` | `/webhook` | Razorpay webhook handler |

### Admin Endpoints

All `/admin/*` routes require the `X-Admin-Token` header.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/admin/products` | List all products (incl. inactive) |
| `POST` | `/admin/products` | Create a product |
| `PATCH` | `/admin/products/:id` | Update a product |
| `DELETE` | `/admin/products/:id` | Delete a product |
| `POST` | `/admin/upload-image` | Upload image to Supabase Storage |
| `GET` | `/admin/purchases` | List all purchase records |

---

## Checkout Flow

```
GET /products          → display catalog
POST /create-order     → get Razorpay order_id + key_id
  ↓ open Razorpay modal
POST /verify-payment   → confirm payment, mark purchase as paid
```

---

## Data Notes

- **Prices** are in **paise** (₹1 = 100 paise) everywhere — in requests, responses, and the database.
- **`image_url`** is always synced to `images[0]` automatically.
- `GET /products` only returns products with `active: true`. Use admin endpoints to manage drafts.

---

## Project Structure

```
├── main.py          # App entry point, public routes, payment logic
├── admin.py         # Admin-only routes (products, purchases)
├── requirements.txt
└── railway.json     # Railway deploy config
```

---

## Supabase Schema

Two tables are required:

**`products`**
```sql
id          uuid primary key default gen_random_uuid()
name        text not null
description text
price       integer not null        -- paise
images      text[]  default '{}'
image_url   text
active      boolean default true
created_at  timestamptz default now()
```

**`purchases`**
```sql
id                   uuid primary key default gen_random_uuid()
razorpay_order_id    text
razorpay_payment_id  text
razorpay_signature   text
customer_name        text
customer_email       text
customer_phone       text
amount               integer         -- paise
currency             text default 'INR'
items                jsonb
status               text default 'pending'  -- pending | paid | failed
created_at           timestamptz default now()
```

---

## Deployment

Deployed via Railway using Nixpacks. Config in `railway.json`:

```json
{
  "deploy": {
    "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT"
  }
}
```

Push to the connected branch to trigger a redeploy.
