# The Yard Containers — Local Prototype

A working local prototype built with Python 3 (standard library only) and SQLite. There are no pip dependencies and nothing to install.

## Run
```bash
cd the_yards_app
python3 seed.py      # optional: creates yards.db with 10 containers (YC-001..010) and demo orders
python3 server.py    # http://localhost:8000
```

**Pages:**

| Page | Address | Notes |
|---|---|---|
| Site and chat widget | `/` | |
| Checkout | `/checkout?product=buy\|rent\|lease\|rto&size=20\|40` | |
| Admin back office | `/admin` | Basic auth: any username, password `ADMIN_PASSWORD` |
| Work order / PO | `/po/<order id>` | |
| Driver view | `/driver/1..3` | |
| Stripe webhook | `POST /webhook/stripe` | |

Delete `yards.db` to reset all data.

## Environment variables
| Var | Purpose | Default |
|---|---|---|
| `STRIPE_SECRET_KEY` | Turns on real Stripe Checkout Sessions: one-time payments for sales, subscriptions for monthly products. **If unset, the app runs in DEMO PAYMENT MODE** (orange banner; payments are simulated; nothing is charged). | unset |
| `STRIPE_WEBHOOK_SECRET` | Verifies the `Stripe-Signature` header (HMAC-SHA256). **Required in production.** If unset, events are accepted unverified; this is for dev only. | unset |
| `OPENAI_API_KEY` | Optional. The chat uses an LLM to rephrase replies; data capture and prices stay rule-based. | unset → scripted fallback |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | Any OpenAI-compatible endpoint and model | `https://api.openai.com/v1` / `gpt-4o-mini` |
| `ADMIN_PASSWORD` | Admin, PO and driver pages | `demo` |
| `ALERT_EMAIL` / `DISPATCH_FROM` / `DISPATCH_REPLY_TO` / `CONTACT_EMAIL` | Admin order alerts; sender and reply-to for driver emails; public contact | `info@theyardcontainers.com` / `dispatch@theyardcontainers.com` / `dispatch@…` / `info@…` |
| `BASE_URL`, `PORT`, `DB_PATH` | Public URL used in payment links and redirects; port; SQLite file | `http://localhost:8000`, `8000`, `./yards.db` |

## How it works
- **Price list:** the `PRICES` dict in `server.py` is the only source of prices. The chat reads prices from it and nowhere else.
  - **Buy:** 20' at $2,095 delivered or $1,945 pickup; 40' at $2,695 delivered or $2,545 pickup.
  - **Storage rental:** $125/mo (20') or $175/mo (40'), plus the ZIP-based delivery fee.
  - **Job-site lease:** $175/mo (20') or $225/mo (40'). Delivery, swap and pickup are $150 each. The 40' rate and the fees are business-plan estimates; set your own.
  - **Rent-to-own:** $111/mo (20') or $139/mo (40') for 36 months, plus $150 delivery. These are estimates benchmarked to a competitor's program; set your own terms.
- **LLM guardrail:** LLM output is rejected if it contains any `$` figure not in the price list.
- **What happens when an order is paid** (demo button or Stripe webhook `checkout.session.completed` / `invoice.paid`):
  1. The order is marked `paid`.
  2. The first available container of the right size is allocated and set to `sold`, `rented` or `leased`. If none is free, the order shows `BACKORDER`.
  3. The job is auto-assigned to the driver with the fewest jobs that day (maximum 4 per driver per day; otherwise it rolls to the next day). Driver *n* uses Truck *n*.
  4. A work order / PO is generated as an HTML page at `/po/<id>`; print to PDF from the browser.
- **Chat secretary** collects, in order:
  - product (buy, rent, lease, rent-to-own) and size;
  - delivered or pickup (buy only);
  - name, phone and email;
  - delivery address and date;
  - site notes.
  - It then confirms the details and creates the order with a payment link. It answers price, condition, access, permit, service area and surface questions from fixed text.
  - Delivery bands: 0–15 mi $150, 15–30 $200, 30–60 $300, 60–100 $350, over 100 call for quote (est.). See /workspace/delivery_rates.md.

## Tested end to end (screenshots in `shots/`)
1. `1_site_chat.png`: chat order flow ending in an order and payment link.
2. `2_checkout_pay.png`: review and pay page in demo mode. `2b_checkout_form.png`: checkout form.
3. Demo payment marks the order paid, assigns Driver 2, and allocates container YC-004.
4. `3_admin_dispatch.png`: dispatch board, orders and inventory.
5. `4_work_order.png`: the generated PO.
6. `5_driver_view.png`: Driver 2's jobs.
7. `6_mobile_chat.png`: mobile view with the chat open.

A simulated Stripe webhook event, posted locally with no real Stripe call, also marked a pending order paid and assigned it.

## What's simulated or missing
- **Payments:** simulated unless `STRIPE_SECRET_KEY` is set. The Stripe code path (Checkout Session creation and the webhook) is written but **has not been run against Stripe**. No keys were used and nothing was charged.
- **Notifications:** no SMS or email is sent.
- **Rent-to-own:** modeled as a plain monthly subscription. There is no automatic stop after 36 months and no title-transfer handling.
- **Swap and pickup fees:** listed on the work order but not billed automatically.
- **Admin auth:** a single shared password. There are no user accounts and no per-driver login.

## To go live
1. **Stripe account.** Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`. Register the webhook URL `https://<domain>/webhook/stripe` for `checkout.session.completed`, `invoice.paid` and `customer.subscription.deleted`. Add a subscription cancel/end flow for rent-to-own term limits. Turn on Stripe Tax if needed.
2. **LLM key** (optional): `OPENAI_API_KEY` or a compatible endpoint. Keep the price guardrail in place.
3. **Domain and hosting.** Use HTTPS behind a reverse proxy (Caddy or nginx) on a small VPS, Fly.io or Render. Back up `yards.db` regularly, or move to Postgres.
4. **Notifications.** Use Twilio (SMS) and SendGrid or Postmark (email) for order confirmations, driver dispatch texts and delivery-day ETAs.
5. **Legal documents.** Have an attorney draft a sales terms/bill of sale, a rental and lease agreement (damage, liability, access, late fees, retrieval) and rent-to-own terms. Show them at checkout and record the customer's acceptance.
6. **Operations.** Get real insurance and DOT compliance in place. Add per-driver logins, a photo and signature capture on delivery, and an inventory purchase flow for new boxes.

## ZIP-based delivery pricing (added)
- **Configuration:** `ORIGIN`, `ROAD_FACTOR` (1.25, est.) and `DELIVERY_BANDS` in `server.py` are the single table to edit.
- **Data:** ZIP centers come from the offline Census 2023 ZCTA Gazetteer in `static/zcta_centroids.csv`.
- **API:** `GET /api/delivery?zip=49503` returns miles, fee, and delivered prices for 20' and 40'. Over 100 road mi it returns `fee: null` and `call_for_quote: true`. Checkout blocks payment, and the chat and site show a call-for-quote message.
- **Where it's used:**
  - Buying: delivered price = pickup price + fee.
  - Rent, lease and rent-to-own: the delivery fee, with swap and pickup at the same fee.
  - The chat asks for the ZIP and quotes from the table.
  - The checkout and the site have ZIP checkers.
  - Each order and work order stores the ZIP, miles and fee.
- **Screenshots:** `shots/7_zip_checker.png` and `shots/8_checkout_zip.png`.

---

## Production features (added Oct 12, 2026)

- **Server:** stdlib `ThreadingHTTPServer` (threaded, no dependencies) with SQLite in WAL mode. Run **one instance**, because SQLite lives on a single disk. `GET /healthz` returns JSON (db ok, payment mode, sms/email enabled).
- **Config:** everything comes from env vars. See `.env.example`. `APP_ENV=production` refuses to start unless `ADMIN_PASSWORD` (not "demo"), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SECRET_KEY` and an `https://` `BASE_URL` are set.
- **Stripe webhook:** when `STRIPE_WEBHOOK_SECRET` is set, every event must carry a valid `Stripe-Signature` (HMAC-SHA256, 5-minute tolerance). Unsigned or bad events get a 400. In production, unsigned events are always refused.
- **Notifications (`notify.py`):** when an order is paid, the customer gets an email and SMS confirmation with the delivery date. The assigned driver gets an email and SMS with the job and a link to `BASE_URL/driver/po/<id>`. Day-before reminders go to both, sent once per order. They run from an in-process loop (every 15 min, after `REMINDER_HOUR`, `REMINDER_LOOP=1`) or from `GET /cron/reminders?token=$CRON_TOKEN`. SMS uses Twilio and email uses SendGrid (or SMTP). **Every message is recorded at `/admin/outbox`**. Status is `sent`, `failed` (with the error), or `logged` (no keys or a placeholder address).
- **Drivers:** driver names, phones, emails and PINs are set at `/admin/settings`. Phones and emails start as placeholders, and PINs start random (or come from `DRIVER1_PIN`…). Drivers sign in at `/driver` with their PIN. They get a signed, HttpOnly 14-day cookie and see **only their own jobs and work orders**, with no access to admin. Admin can still view any driver's board at `/admin/driver/N`.

## Go-live checklist (step by step)

None of these steps were done for you: no accounts were created and nothing was bought.

1. **Code → GitHub.** Push this folder to a private repo. `.dockerignore` keeps `yards.db`, logs and screenshots out of the image.
2. **Stripe.**
   1. Finish account activation (business details, bank account). Test first with `sk_test_…` keys.
   2. Developers → API keys: copy `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY`.
   3. Developers → Webhooks → Add endpoint `https://YOURDOMAIN/webhook/stripe` with events `checkout.session.completed` and `invoice.paid`. Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
   4. Settings → Customer emails: turn on receipts. Settings → Billing: set up the customer portal so renters can update cards.
   5. Test mode: place an order, pay with card 4242 4242 4242 4242, and confirm the order shows as paid in `/admin` and the messages appear in `/admin/outbox`. Then switch to live keys.
3. **Render.**
   1. New → Blueprint → pick the repo. `render.yaml` creates a Docker web service with a 1 GB disk at `/data` (`DB_PATH=/data/yards.db`), health check at `/healthz`, and one instance. A persistent disk needs a paid instance (Starter).
   2. Fill in the `sync: false` env vars (Stripe, Twilio, SendGrid, `BASE_URL`, `MAIL_FROM`). `ADMIN_PASSWORD`, `SECRET_KEY` and `CRON_TOKEN` are auto-generated; read them in the dashboard.
   3. Deploy, then open `https://<service>.onrender.com/healthz`.
   4. Optional backup trigger for reminders: a Render Cron Job or any uptime pinger hitting `/cron/reminders?token=…` hourly.
   5. Back up the disk: Render takes daily disk snapshots. Download a copy of `/data/yards.db` occasionally via the Shell tab.
4. **Domain DNS.** Render → Settings → Custom Domains → add `theyardcontainers.com` (and `www.`).
   - At your registrar, add `CNAME www → <service>.onrender.com`.
   - For the apex, use `ALIAS`/`ANAME` → `<service>.onrender.com`, or an `A` record to the IP Render shows.
   - Render issues TLS automatically. Then set `BASE_URL=https://theyardcontainers.com` and update the Stripe webhook URL to match.
5. **Twilio (SMS).**
   1. Buy a local 269 number.
   2. **Register for A2P 10DLC** (brand plus campaign "customer care / delivery notifications"). US carriers block unregistered business SMS, and approval takes days.
   3. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` (E.164) or `TWILIO_MESSAGING_SERVICE_SID`.
   4. Add an SMS consent line to checkout before turning this on.
6. **SendGrid (email).**
   1. Settings → Sender Authentication → **authenticate your domain**: add the CNAME records it gives you to DNS (SPF/DKIM), plus a DMARC TXT record (`_dmarc` → `v=DMARC1; p=none; rua=mailto:info@theyardcontainers.com`).
   2. Create an API key with Mail Send permission and set it as `SENDGRID_API_KEY`. Set `MAIL_FROM=The Yard Containers <info@theyardcontainers.com>`.
   3. Instead of SendGrid you can use any SMTP provider: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`.
7. **Final setup.**
   1. Log in at `/admin` (user: any, password `ADMIN_PASSWORD`), open `/admin/settings`, and enter real driver phones (+1…), emails and PINs.
   2. Phone placeholders are filled with (855) 768-YARD (+18557689273); email is info@theyardcontainers.com.
   3. Place one real low-value order, check `/admin/outbox` shows `sent`, then refund it.


## Product feature flag
`ENABLED_PRODUCTS` (comma list of buy, rent, lease, rto; default `buy`). Sales only by default: homepage rental/lease/RTO sections, nav, FAQ, checkout options and chat prompts are hidden, and `/order`, chat and `/api/voice/order` reject non-enabled products (voice returns 400 `product_not_offered`). Rental code is intact; set e.g. `ENABLED_PRODUCTS=buy,rent` on Render to turn rentals back on. Homepage blocks are marked `<!--IF:rentals-->` / `<!--IF:salesonly-->` in static/index.html.
