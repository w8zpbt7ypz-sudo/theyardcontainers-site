#!/usr/bin/env python3
"""The Yard Containers — local prototype. Python stdlib + SQLite only."""
import notify, secrets, threading, logging
import os, json, sqlite3, base64, hmac, hashlib, time, uuid, re, html, datetime, urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DB_PATH", os.path.join(ROOT, "yards.db"))
PORT = int(os.environ.get("PORT", "8000"))
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{PORT}")
CANONICAL_URL = os.environ.get("CANONICAL_URL", "https://theyardcontainers.com")  # public site domain
STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WHSEC = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
LLM_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
LLM_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "demo")  # "demo" is for local only; production refuses to start with it
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
PRODUCTION = os.environ.get("APP_ENV", "").lower() == "production"
SECRET_KEY = os.environ.get("SECRET_KEY") or hashlib.sha256(("yards:" + ADMIN_PW).encode()).hexdigest()
CRON_TOKEN = os.environ.get("CRON_TOKEN", "")
VOICE_SECRET = os.environ.get("VOICE_SECRET", "")  # shared secret for the AI phone assistant (X-Voice-Secret)
REMINDER_LOOP = os.environ.get("REMINDER_LOOP", "1") == "1"   # in-process day-before reminder loop
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "9"))        # local hour after which day-before reminders go out
DRIVERS = (1, 2, 3)
DEMO = not STRIPE_KEY

# ---- Single source of truth for prices (USD). Chat may only quote from here. ----
PRICES = {
 "buy":   {"20": {"pickup": 1945}, "40": {"pickup": 2545}},   # delivered = pickup price + ZIP delivery fee
 "rent":  {"20": {"monthly": 125}, "40": {"monthly": 175} },
 "lease": {"20": {"monthly": 175}, "40": {"monthly": 225}},   # delivery / swap / pickup each = ZIP delivery fee
 "rto":   {"20": {"monthly": 111, "months": 36}, "40": {"monthly": 139, "months": 36}},
}
# ---- Delivery pricing (est.) — edit this ONE table. (max_miles, base_fee, per_mile_over, over_miles) ----
ORIGIN = (42.288819, -85.518205)   # 5672 E Michigan Ave, Comstock MI 49048 (US Census geocoder)
ROAD_FACTOR = 1.25                 # straight-line -> road miles (est.)
DELIVERY_BANDS = [                 # last band ends at 100 mi; beyond = call for quote
    (15,  150, 0,    0),
    (30,  200, 0,    0),
    (60,  250, 0,    0),
    (100, 300, 0,    0),
]                                  # beyond last band (100 mi) = call for quote
WESTERN_DELIVERY_FEE = 1272        # competitor benchmark (user-supplied)
ZIPS = {}
def load_zips():
    import csv
    with open(os.path.join(ROOT, "static", "zcta_centroids.csv")) as f:
        for r in csv.DictReader(f): ZIPS[r["zip"]] = (float(r["lat"]), float(r["lon"]))
def haversine(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 3958.8 * 2 * math.asin(math.sqrt(h))
def quote_delivery(zipc):
    """Returns {'zip','miles','fee'} ; fee None => call for quote ; error if unknown ZIP."""
    zipc = (zipc or "").strip()[:5]
    if not ZIPS: load_zips()
    if zipc not in ZIPS: return {"zip": zipc, "error": "Unknown ZIP"}
    miles = round(haversine(ORIGIN, ZIPS[zipc]) * ROAD_FACTOR, 1)
    for mx, base, per, over in DELIVERY_BANDS:
        if miles <= mx:
            return {"zip": zipc, "miles": miles, "fee": int(round(base + max(0, miles - over) * per)) if per else base}
    return {"zip": zipc, "miles": miles, "fee": None}

LABEL = {"buy": "Buy", "rent": "Storage rental", "lease": "Job-site lease", "rto": "Rent-to-own"}
FAQ = {
 "wind": "Wind & watertight means no holes, working doors and seals; it may show dents and surface rust.",
 "access": "Delivery needs about 70–80 ft of straight, level access for a 20' and 120–130 ft for a 40', plus overhead clearance.",
 "permit": "Permit rules vary by township/city — please check with your local zoning office.",
 "area": "We deliver within 30 miles of Comstock, MI. Farther deliveries are quoted separately by our team.",
 "surface": "Firm, level ground; gravel or blocks under the corners work well.",
}

def db():
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, created TEXT, product TEXT, size TEXT, mode TEXT,
      name TEXT, phone TEXT, email TEXT, address TEXT, date TEXT, notes TEXT,
      one_time INTEGER, monthly INTEGER, status TEXT, stripe_session TEXT, container_id TEXT, driver INTEGER,
      zip TEXT, miles REAL, delivery_fee INTEGER);
    CREATE TABLE IF NOT EXISTS containers(id TEXT PRIMARY KEY, size TEXT, status TEXT, order_id TEXT);
    CREATE TABLE IF NOT EXISTS chats(sid TEXT PRIMARY KEY, state TEXT);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    """)
    c.execute("PRAGMA journal_mode=WAL")
    notify.init(c)
    for i in DRIVERS:   # placeholders; edit in /admin/settings
        for k, v in (("name", f"Driver {i}"), ("phone", f"[driver {i} phone]"), ("email", f"[driver {i} email]"),
                     ("pin", os.environ.get(f"DRIVER{i}_PIN") or f"{secrets.randbelow(900000)+100000}")):
            c.execute("insert or ignore into settings values(?,?)", (f"driver{i}_{k}", v))
    if not c.execute("select count(*) from containers").fetchone()[0]:
        for i in range(1, 11):
            c.execute("insert into containers values(?,?,?,NULL)", (f"YC-{i:03d}", "20" if i <= 5 else "40", "available"))
    c.commit(); c.close()

def price_order(product, size, mode, fee):
    """fee = ZIP delivery fee (0 for pickup). Returns (one_time, monthly)."""
    if product == "buy":
        return PRICES["buy"][size]["pickup"] + (fee if mode == "delivered" else 0), 0
    return fee, PRICES[product][size]["monthly"]

def create_order(d):
    """Returns order id, or raises ValueError (unknown ZIP / call for quote)."""
    product, size = d["product"], d["size"]
    mode = d.get("mode") if product == "buy" else "delivered"
    mode = mode or "delivered"
    q = {"zip": d.get("zip",""), "miles": None, "fee": 0}
    if mode == "delivered":
        q = quote_delivery(d.get("zip", ""))
        if q.get("error"): raise ValueError("Unknown ZIP code")
        if q["fee"] is None: raise ValueError(f"{q['miles']} mi is beyond our 100-mile delivery area — call for a quote")
    one, mon = price_order(product, size, mode, q["fee"])
    oid = "ORD-" + uuid.uuid4().hex[:6].upper()
    c = db()
    c.execute("insert into orders values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (oid, datetime.datetime.now().isoformat(timespec="seconds"),
        product, size, mode, d.get("name",""), d.get("phone",""), d.get("email",""), d.get("address",""),
        d.get("date") or (datetime.date.today() + datetime.timedelta(days=2)).isoformat(), d.get("notes",""), one, mon, "pending", None, None, None,
        q.get("zip"), q.get("miles"), q.get("fee")))
    c.commit(); c.close(); return oid

def assign_driver(c, date):
    # Auto-assign: driver with fewest jobs that date; max 4 jobs/driver/day, else roll to next day.
    d = datetime.date.fromisoformat(date)
    for _ in range(30):
        loads = {i: c.execute("select count(*) from orders where driver=? and date=? and status='paid'", (i, d.isoformat())).fetchone()[0] for i in (1, 2, 3)}
        drv = min(loads, key=lambda k: (loads[k], k))
        if loads[drv] < 4: return drv, d.isoformat()
        d += datetime.timedelta(days=1)
    return 1, d.isoformat()

def mark_paid(oid, session=None):
    c = db(); o = c.execute("select * from orders where id=?", (oid,)).fetchone()
    if not o or o["status"] == "paid": c.close(); return
    status = {"buy": "sold", "rent": "rented", "lease": "leased", "rto": "rented"}[o["product"]]
    box = c.execute("select id from containers where size=? and status='available' order by id limit 1", (o["size"],)).fetchone()
    cid = box["id"] if box else None
    if cid: c.execute("update containers set status=?, order_id=? where id=?", (status, oid, cid))
    drv, date = assign_driver(c, o["date"])
    c.execute("update orders set status='paid', stripe_session=coalesce(?,stripe_session), container_id=?, driver=?, date=? where id=?",
              (session, cid or "BACKORDER (source new box)", drv, date, oid))
    o = c.execute("select * from orders where id=?", (oid,)).fetchone()
    notify_paid(c, o)
    c.commit(); c.close()

# ---------------- Stripe (REST via urllib; only used if STRIPE_SECRET_KEY set) ----------------
def stripe_checkout(o):
    name = f"{LABEL[o['product']]} – {o['size']}' container"
    form = [("mode", "subscription" if o["monthly"] else "payment"),
            ("success_url", f"{BASE_URL}/success/{o['id']}"), ("cancel_url", f"{BASE_URL}/pay/{o['id']}"),
            ("client_reference_id", o["id"]), ("metadata[order_id]", o["id"]), ("customer_email", o["email"] or "")]
    i = 0
    if o["monthly"]:
        form += [(f"line_items[{i}][price_data][currency]", "usd"), (f"line_items[{i}][price_data][product_data][name]", name + " (monthly)"),
                 (f"line_items[{i}][price_data][unit_amount]", str(o["monthly"]*100)), (f"line_items[{i}][price_data][recurring][interval]", "month"),
                 (f"line_items[{i}][quantity]", "1"), ("subscription_data[metadata][order_id]", o["id"])]
        i += 1
    if o["one_time"]:
        form += [(f"line_items[{i}][price_data][currency]", "usd"),
                 (f"line_items[{i}][price_data][product_data][name]", name if not o["monthly"] else "Delivery fee"),
                 (f"line_items[{i}][price_data][unit_amount]", str(o["one_time"]*100)), (f"line_items[{i}][quantity]", "1")]
    req = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions", data=urllib.parse.urlencode(form).encode(),
        headers={"Authorization": "Bearer " + STRIPE_KEY})
    return json.load(urllib.request.urlopen(req, timeout=20))

def verify_stripe_sig(payload, header):
    if not STRIPE_WHSEC: return not PRODUCTION   # unsigned events only accepted outside production
    if not header: return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    signed = f"{parts.get('t')}.{payload.decode()}".encode()
    exp = hmac.new(STRIPE_WHSEC.encode(), signed, hashlib.sha256).hexdigest()
    sigs = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("v1=")]
    try: ts_ok = abs(time.time() - int(parts.get("t", 0))) < 300
    except ValueError: return False
    return ts_ok and any(hmac.compare_digest(exp, s) for s in sigs)

# ---------------- Settings, drivers, notifications ----------------
def setting(c, k, default=""):
    r = c.execute("select v from settings where k=?", (k,)).fetchone(); return r["v"] if r else default
def driver_info(c, i):
    return {k: setting(c, f"driver{i}_{k}") for k in ("name", "phone", "email", "pin")}

VOICE_FIELDS = ("name", "phone", "email", "address", "zip", "size", "product", "mode", "date", "notes")
def voice_order(d):
    """Validate a phone-assistant order. Returns (status, dict)."""
    if not isinstance(d, dict): return 400, {"error": "JSON object expected"}
    d = {k: str(d.get(k) or "").strip() for k in VOICE_FIELDS}
    d["size"] = d["size"].rstrip("'").replace("ft", "").strip()
    d["product"] = d["product"].lower(); d["mode"] = (d["mode"] or "delivered").lower()
    if d["size"] not in ("20", "40"): return 400, {"error": "size must be 20 or 40"}
    if d["product"] not in LABEL: return 400, {"error": "product must be buy, rent, lease or rto"}
    if d["mode"] not in ("delivered", "pickup"): return 400, {"error": "mode must be delivered or pickup"}
    if d["product"] != "buy": d["mode"] = "delivered"
    if not d["name"] or not d["phone"]: return 400, {"error": "name and phone are required"}
    if d["mode"] == "delivered" and not d["zip"]: return 400, {"error": "zip is required for delivery"}
    if d["date"]:
        try: datetime.date.fromisoformat(d["date"])
        except ValueError: return 400, {"error": "date must be YYYY-MM-DD"}
    d["notes"] = ("[phone order] " + d["notes"]).strip()
    try: oid = create_order(d)
    except ValueError as e:
        q = quote_delivery(d["zip"]); code = "unknown_zip" if q.get("error") else "call_for_quote"
        return 422, {"error": str(e), "code": code, "miles": q.get("miles")}
    c = db(); o = c.execute("select * from orders where id=?", (oid,)).fetchone(); c.close()
    totals = {"one_time": o["one_time"], "monthly": o["monthly"], "delivery_fee": o["delivery_fee"], "miles": o["miles"]}
    pay_url = f"{BASE_URL}/pay/{oid}"
    alert = (f"NEW PHONE ORDER {oid} (unpaid): {LABEL[o['product']]} {o['size']}' container, {o['mode']}, {o['date']} -> {o['address'] or '-'} "
             f"(ZIP {o['zip'] or '-'}). Customer {o['name']} {o['phone']} {o['email'] or '-'}. Due now ${o['one_time']}"
             + (f", then ${o['monthly']}/mo" if o["monthly"] else "") + f". Notes: {o['notes']}. Pay link: {pay_url}  Admin: {BASE_URL}/admin")
    def _alert():
        c2 = db()
        try: notify.send(c2, "email", notify.ALERT_EMAIL, f"New phone order {oid} — {o['name']}", alert, "alert_voice", oid); c2.commit()
        except Exception as e: logging.warning("voice alert failed: %s", e)
        finally: c2.close()
    threading.Thread(target=_alert, daemon=True).start()   # don't hold the caller while mail goes out
    return 200, {"order_id": oid, "totals": totals, "pay_url": pay_url}

def notify_paid(c, o):
    d = driver_info(c, o["driver"]); when = o["date"]
    what = f"{LABEL[o['product']]} {o['size']}' container"
    cust = (f"The Yard Containers: order {o['id']} is paid. Your {what} is scheduled for {when}"
            + (f" to {o['address']}." if o["mode"] == "delivered" else " for pickup at our yard (5672 E Michigan Ave, Comstock).")
            + " Questions? Reply or call [phone].")
    notify.send(c, "email", o["email"], f"Order {o['id']} confirmed — {when}", cust, "confirm_customer", o["id"])
    notify.send(c, "sms", o["phone"], "", cust, "confirm_customer", o["id"])
    link = f"{BASE_URL}/driver/po/{o['id']}"
    job = f"New job {o['id']} on {when}: {what} -> {o['address']} (ZIP {o['zip'] or '-'}). Customer {o['name']} {o['phone']}. Work order: {link}"
    notify.send(c, "email", d["email"], f"New job {o['id']} — {when}", job, "job_driver", o["id"], notify.DISPATCH_FROM, notify.DISPATCH_REPLY_TO)
    notify.send(c, "sms", d["phone"], "", job, "job_driver", o["id"])
    alert = f"PAID order {o['id']}: {what}, {o['mode'] or '-'}, {when} -> {o['address']} (ZIP {o['zip'] or '-'}). Customer {o['name']} {o['phone']} {o['email']}. Driver {d['name'] or o['driver']}. Admin: {BASE_URL}/admin"
    notify.send(c, "email", notify.ALERT_EMAIL, f"New paid order {o['id']} — {when}", alert, "alert_admin", o["id"])

def send_reminders(today=None, force=False):
    """Day-before reminders for paid orders dated tomorrow. Idempotent (one per order). Returns count."""
    today = today or datetime.date.today()
    if not force and datetime.datetime.now().hour < REMINDER_HOUR and today == datetime.date.today(): return 0
    tomorrow = (today + datetime.timedelta(days=1)).isoformat(); n = 0
    c = db()
    for o in c.execute("select * from orders where status='paid' and date=?", (tomorrow,)).fetchall():
        if c.execute("select 1 from outbox where kind like 'reminder%' and order_id=?", (o["id"],)).fetchone(): continue
        d = driver_info(c, o["driver"])
        msg = f"Reminder from The Yard Containers: your {LABEL[o['product']]} {o['size']}' container ({o['id']}) is scheduled for tomorrow, {o['date']}. Please make sure the site is clear and accessible."
        notify.send(c, "email", o["email"], f"Tomorrow: order {o['id']}", msg, "reminder_customer", o["id"])
        notify.send(c, "sms", o["phone"], "", msg, "reminder_customer", o["id"])
        jm = f"Reminder: job {o['id']} tomorrow {o['date']} -> {o['address']}. Work order: {BASE_URL}/driver/po/{o['id']}"
        notify.send(c, "email", d["email"], f"Tomorrow: job {o['id']}", jm, "reminder_driver", o["id"], notify.DISPATCH_FROM, notify.DISPATCH_REPLY_TO)
        notify.send(c, "sms", d["phone"], "", jm, "reminder_driver", o["id"]); n += 1
    c.commit(); c.close(); return n

def reminder_loop():
    while True:
        try: send_reminders()
        except Exception as e: logging.exception("reminder loop: %s", e)
        time.sleep(900)

def driver_cookie(i):
    exp = int(time.time()) + 14 * 86400; v = f"{i}.{exp}"
    return v + "." + hmac.new(SECRET_KEY.encode(), v.encode(), hashlib.sha256).hexdigest()[:32]
def check_driver_cookie(v):
    try:
        i, exp, sig = v.split(".")
        good = hmac.new(SECRET_KEY.encode(), f"{i}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        return int(i) if hmac.compare_digest(good, sig) and int(exp) > time.time() else None
    except Exception: return None

def outbox_page():
    c = db(); rows = c.execute("select * from outbox order by id desc limit 300").fetchall(); c.close()
    mode = f"SMS: {'Twilio' if notify.sms_enabled() else 'log only'} · Email: {'SendGrid' if notify.SENDGRID_KEY else 'SMTP' if notify.SMTP_HOST else 'log only'}"
    t = "<table class=t><tr><th>#</th><th>When</th><th>Kind</th><th>Ch</th><th>To</th><th>Status</th><th>Message</th></tr>" + "".join(
        f"<tr><td>{r['id']}</td><td>{esc(r['created'])}</td><td>{esc(r['kind'])}<br>{esc(r['order_id'])}</td><td>{r['channel']}</td><td>{esc(r['to_addr'])}</td><td>{r['status']}{'<br><small>'+esc(r['error'])+'</small>' if r['error'] else ''}</td><td>{('<b>'+esc(r['subject'])+'</b><br>') if r['subject'] else ''}{esc(r['body'])}</td></tr>" for r in rows) + "</table>"
    return page("Outbox", f"<div class=eyebrow>Notifications</div><h2>Outbox</h2><p class=sub>{mode}</p><form method=post action=/admin/run-reminders><button class=btn>Run day-before reminders now</button></form><br>{t}", "<a href='/admin'>Admin</a>")

def settings_page(msg=""):
    c = db(); ds = {i: driver_info(c, i) for i in DRIVERS}; c.close()
    rows = "".join(f"<tr><td>{i}</td>" + "".join(f"<td><input name=driver{i}_{k} value='{esc(ds[i][k])}'></td>" for k in ("name","phone","email","pin")) + "</tr>" for i in DRIVERS)
    return page("Settings", f"""<div class=eyebrow>Settings</div><h2>Drivers</h2>{'<p><b>'+esc(msg)+'</b></p>' if msg else ''}
<form method=post action=/admin/settings><table class=t><tr><th>#</th><th>Name</th><th>Phone (E.164, e.g. +12695551234)</th><th>Email</th><th>PIN (4–8 digits)</th></tr>{rows}</table>
<p><button class='btn p'>Save</button></p></form><p class=sub>Drivers sign in at <a href=/driver>/driver</a> with their PIN and see only their own jobs.</p>""", "<a href='/admin'>Admin</a>")

def driver_login_page(err=""):
    return page("Driver sign-in", f"""<div class=eyebrow>Drivers</div><h2>Sign in</h2>{'<p><b>'+esc(err)+'</b></p>' if err else ''}
<form method=post action=/driver/login class=card style='max-width:360px'><label>PIN<br><input name=pin type=password inputmode=numeric autocomplete=off required></label><p><button class='btn p'>Sign in</button></p></form>""")

# ---------------- Chat secretary ----------------
STEPS = ["product", "size", "mode", "zip", "name", "phone", "email", "address", "date", "notes", "confirm"]
ASK = {
 "product": "Are you looking to buy, rent for storage, lease for a job site, or rent-to-own?",
 "size": "20' or 40'?", "mode": "Delivered (priced by ZIP) or pickup at our yard?",
 "zip": "What ZIP code is it going to? I'll quote delivery.", "name": "What's your name?", "phone": "Best phone number?", "email": "And your email?",
 "address": "Street address for delivery?", "date": "Preferred delivery date (YYYY-MM-DD)?",
 "notes": "Any site conditions? (ground surface, access width, overhead wires, gate) — or say 'none'.",
}
def bands_text():
    out = []; lo = 0
    for mx, base, per, over in DELIVERY_BANDS:
        out.append(f"{lo}-{mx} mi ${base}" + (f" + ${per:.2f}/mi over {over}" if per else "")); lo = mx
    return "; ".join(out) + f"; over {lo} mi call for quote"

def price_sheet():
    b, r, l, t = PRICES["buy"], PRICES["rent"], PRICES["lease"], PRICES["rto"]
    return (f"Buy: 20' ${b['20']['pickup']:,}, 40' ${b['40']['pickup']:,} at our yard, plus delivery by ZIP. "
            f"Storage rental: 20' ${r['20']['monthly']}/mo, 40' ${r['40']['monthly']}/mo. "
            f"Job-site lease: 20' ${l['20']['monthly']}/mo, 40' ${l['40']['monthly']}/mo. "
            f"Rent-to-own: 20' ${t['20']['monthly']}/mo, 40' ${t['40']['monthly']}/mo for {t['20']['months']} months. "
            f"Delivery (also rental pickup/swap) by road miles from Comstock: {bands_text()}.")

def faq_answer(m):
    m = m.lower(); out = []
    if re.search(r"price|cost|how much|\$|rate", m): out.append(price_sheet())
    if "watertight" in m or "condition" in m: out.append(FAQ["wind"])
    if re.search(r"access|room|space|clearance", m): out.append(FAQ["access"])
    if "permit" in m or "zoning" in m: out.append(FAQ["permit"])
    if re.search(r"area|far|miles|deliver to", m): out.append(FAQ["area"])
    if re.search(r"surface|ground|level", m): out.append(FAQ["surface"])
    return " ".join(out)

def llm_rephrase(text, user_msg):
    """Optional: polish replies with an LLM. Prices are injected verbatim; model told never to add prices."""
    if not LLM_KEY: return text
    try:
        body = {"model": LLM_MODEL, "temperature": 0.2, "messages": [
            {"role": "system", "content": "You are the friendly secretary for The Yard Containers Rewrite the DRAFT reply naturally in 1-3 sentences. "
             "Never add, change or estimate any price, fee or term; only use numbers appearing in the DRAFT. Keep the final question. Price list: " + price_sheet()},
            {"role": "user", "content": f"Customer said: {user_msg}\nDRAFT: {text}"}]}
        req = urllib.request.Request(LLM_BASE.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
              headers={"Authorization": "Bearer " + LLM_KEY, "Content-Type": "application/json"})
        out = json.load(urllib.request.urlopen(req, timeout=20))["choices"][0]["message"]["content"]
        nums = set(re.findall(r"\$[\d,]+", out)); allowed = set(re.findall(r"\$[\d,]+", text + price_sheet()))
        return out if nums <= allowed else text  # guardrail: reject any invented $ figure
    except Exception:
        return text

def chat(sid, msg):
    c = db(); row = c.execute("select state from chats where sid=?", (sid,)).fetchone()
    st = json.loads(row["state"]) if row else {"step": "product", "d": {}}
    d, m, low = st["d"], msg.strip(), msg.strip().lower()
    pre = faq_answer(m) if st["step"] not in ("name", "phone", "email", "address", "notes", "zip") else ""
    reply = ""
    s = st["step"]
    if s == "product":
        for k, pat in [("rto", r"rent.?to.?own|rto"), ("lease", r"lease|job.?site"), ("rent", r"rent|storage"), ("buy", r"buy|purchase")]:
            if re.search(pat, low): d["product"] = k; break
    elif s == "size":
        mm = re.search(r"\b(20|40)\b", low)
        if mm: d["size"] = mm.group(1)
    elif s == "mode":
        if "pick" in low: d["mode"] = "pickup"
        elif "deliver" in low: d["mode"] = "delivered"
    elif s == "zip" and re.search(r"\b\d{5}\b", m):
        q = quote_delivery(re.search(r"\b\d{5}\b", m).group(0))
        if q.get("error"): reply = "I couldn't find that ZIP. " + ASK["zip"]
        elif q["fee"] is None:
            reply = f"ZIP {q['zip']} is about {q['miles']:.0f} road miles from Comstock — beyond our 100-mile delivery area, so our team will need to quote it. Please call [phone]."
            st["step"] = "quote"
        else:
            d["zip"] = q["zip"]; pre = f"Delivery to {q['zip']} (about {q['miles']:.0f} road mi): ${q['fee']}."
    elif s == "name" and len(m) > 1: d["name"] = m
    elif s == "phone" and len(re.sub(r"\D", "", m)) >= 10: d["phone"] = m
    elif s == "email" and re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", m): d["email"] = m
    elif s == "address" and len(m) > 3: d["address"] = m
    elif s == "date" and re.match(r"\d{4}-\d{2}-\d{2}$", m): d["date"] = m
    elif s == "notes": d["notes"] = "" if low in ("none", "no") else m
    elif s == "confirm":
        if low.startswith("y"):
            oid = create_order(d); st = {"step": "done", "d": d, "order": oid}
            reply = f"Order {oid} created. Pay securely here: {BASE_URL}/pay/{oid} — once paid we'll schedule your driver and send a work order."
        elif low.startswith("n"):
            st = {"step": "product", "d": {}}; reply = "No problem — let's start over. " + ASK["product"]
    elif s == "quote":
        reply = "Our team will quote that delivery by phone at [phone]. Anything else I can help with?"
    elif s == "done":
        reply = f"Your order is {st.get('order')}. Payment link: {BASE_URL}/pay/{st.get('order')}"
    if not reply:
        # advance to next missing step
        for step in STEPS:
            if step == "mode" and d.get("product") != "buy": continue
            if step == "zip" and d.get("mode") == "pickup": continue
            if step == "notes" and "notes" in d: continue
            if step == "confirm":
                fee = 0 if d.get("mode") == "pickup" else quote_delivery(d["zip"])["fee"]
                one, mon = price_order(d["product"], d["size"], d.get("mode", "delivered"), fee)
                money = (f"${one:,} total" + (f" (box ${one-fee:,} + delivery ${fee})" if fee else "") if not mon
                         else f"${mon}/mo + ${one} delivery" + (f" (pickup/swap ${one} each)" if d['product'] in ('rent','lease') else ""))
                reply = (f"Summary: {LABEL[d['product']]}, {d['size']}'{', '+d['mode'] if d.get('mode') else ''} — {money}. "
                         f"{d['name']}, {d['phone']}, {d['email']}, {d['address']} on {d['date']}. Notes: {d.get('notes') or 'none'}. Create the order? (yes/no)")
                st["step"] = "confirm"; break
            if step not in d:
                reply = ("Sorry, I didn't catch that. " if (s == step and not pre) else "") + ASK[step]; st["step"] = step; break
    reply = (pre + " " + reply).strip() if pre else reply
    c.execute("insert or replace into chats values(?,?)", (sid, json.dumps(st))); c.commit(); c.close()
    return llm_rephrase(reply, m)

# ---------------- HTML ----------------
CSS = open(os.path.join(ROOT, "static", "style.css")).read()
def page(title, body, sub=""):
    return f"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style></head>
<body><header><div class="wrap nav"><a href="/" class="logo">THE YARD <span>CONTAINERS</span></a><nav><a href="/#buy">Buy</a><a href="/#rent">Rent</a><a href="/#jobsite">Job-site</a>{sub}</nav></div></header>
{('<div class="demo-bar">DEMO PAYMENT MODE — no Stripe key set; payments are simulated, nothing is charged.</div>' if DEMO else '')}
<main class="wrap" style="padding:48px 24px">{body}</main></body></html>"""

def esc(x): return html.escape(str(x if x is not None else ""))

def checkout_form(q):
    product = q.get("product", ["buy"])[0]; size = q.get("size", ["20"])[0]
    opts = "".join(f"<option value={k} {'selected' if k==product else ''}>{v}</option>" for k, v in LABEL.items())
    return page("Checkout", f"""<div class=eyebrow>Checkout</div><h2>Order a container</h2><p class=sub>{esc(price_sheet())}</p>
<form method=post action="/order" class=form>
<div><label>Product</label><select name=product>{opts}</select></div>
<div><label>Size</label><select name=size><option {'selected' if size=='20' else ''}>20</option><option {'selected' if size=='40' else ''}>40</option></select></div>
<div><label>Buy only: delivery</label><select name=mode><option value=delivered>Delivered (fee by ZIP)</option><option value=pickup>Yard pickup</option></select></div>
<div><label>Delivery ZIP</label><input name=zip id=zip maxlength=5 oninput="zq(this.value)"><div id=zres class=sub></div></div>
<div><label>Date</label><input name=date type=date required></div>
<div><label>Name</label><input name=name required></div><div><label>Phone</label><input name=phone required></div>
<div><label>Email</label><input name=email type=email required></div><div><label>Delivery address</label><input name=address required></div>
<div class=full><label>Site conditions / notes</label><textarea name=notes rows=3></textarea></div>
<div class=full><button class="btn p">Continue to payment</button></div></form>
<script>async function zq(v){{if(v.length<5){{zres.textContent='';return}}const r=await (await fetch('/api/delivery?zip='+v)).json();
zres.textContent=r.error?r.error:(r.fee==null?`${{r.miles}} road mi — over 100 mi: call for a quote at [phone]. Online payment not available.`:`${{r.miles}} road mi · delivery $${{r.fee}}`);
const b=document.querySelector('form.form button');const m=document.querySelector('[name=mode]');const block=r.fee==null&&!(m&&m.value=='pickup');b.disabled=block;b.textContent=block?'Call for quote — [phone]':'Continue to payment'}}</script>""")

def pay_page(o):
    one, mon = o["one_time"], o["monthly"]
    dl = f"ZIP {o['zip']}, ~{o['miles']} road mi" if o['zip'] else "yard pickup"
    lines = (f"<li>{LABEL[o['product']]} {o['size']}' ({o['mode']}) — box ${one-(o['delivery_fee'] or 0):,}</li><li>Delivery ({esc(dl)}) ${o['delivery_fee'] or 0}</li><li>Total <b>${one:,}</b></li>" if not mon else
             f"<li>{LABEL[o['product']]} {o['size']}' — <b>${mon}/month</b> (subscription)</li><li>Delivery fee ({esc(dl)}) <b>${one}</b> (one-time)</li>")
    btn = ("<form method=post action='/demo/pay/%s'><button class='btn p'>Simulate successful payment (demo)</button></form>" % o["id"]) if DEMO else \
          ("<form method=post action='/stripe/session/%s'><button class='btn p'>Pay with Stripe</button></form>" % o["id"])
    return page("Pay", f"""<div class=eyebrow>Order {esc(o['id'])}</div><h2>Review &amp; pay</h2>
<div class=card><ul class=l>{lines}</ul><p class=sub>{esc(o['name'])} · {esc(o['address'])} · requested {esc(o['date'])}</p>
<p class=sub>Status: <b>{esc(o['status'])}</b></p>{btn if o['status']!='paid' else '<p>Paid ✔ — <a href="/success/'+o['id']+'">view</a></p>'}</div>""")

def po_html(o):
    fees = f"<tr><td>Delivery</td><td>ZIP {esc(o['zip'])} · ~{o['miles']} road mi (est., straight-line × {ROAD_FACTOR}) · fee ${o['delivery_fee']}</td></tr>" if o['zip'] else "<tr><td>Delivery</td><td>Yard pickup</td></tr>"
    if o["product"] in ("rent", "lease", "rto"): fees += f"<tr><td>Swap / pickup fee (as needed)</td><td>${o['delivery_fee']} each (same band)</td></tr>"
    return page(f"Work Order {o['id']}", f"""<div class=eyebrow>Work order / PO</div><h2>{esc(o['id'])}</h2>
<table class=t><tr><td>Created</td><td>{esc(o['created'])}</td></tr><tr><td>Customer</td><td>{esc(o['name'])} · {esc(o['phone'])} · {esc(o['email'])}</td></tr>
<tr><td>Service</td><td>{LABEL[o['product']]} — {o['size']}' ({esc(o['mode'])})</td></tr><tr><td>Container</td><td>{esc(o['container_id'])}</td></tr>
<tr><td>Deliver to</td><td>{esc(o['address'])}</td></tr><tr><td>Scheduled</td><td>{esc(o['date'])}</td></tr>
<tr><td>Assigned</td><td>Driver {o['driver']} · Truck {o['driver']}</td></tr><tr><td>Site notes</td><td>{esc(o['notes']) or '—'}</td></tr>
<tr><td>Charges</td><td>{'$%s one-time' % format(o['one_time'],',') if not o['monthly'] else '$%s/mo + $%s delivery' % (o['monthly'], o['one_time'])}</td></tr>{fees}
<tr><td>Payment</td><td>{esc(o['status'])}{' (DEMO — simulated)' if (o['stripe_session'] or '').startswith('demo') else ''}</td></tr></table>
<p class=sub style="margin-top:24px">Driver checklist: confirm access (20': ~70–80 ft, 40': ~120–130 ft straight), overhead clearance, level ground; photo container on drop; customer signature.</p>
<p class=sub>Signature: ______________________ Date: __________</p>""", "<a href='/admin'>Admin</a>")

def admin_page():
    c = db(); orders = c.execute("select * from orders order by date, driver").fetchall(); boxes = c.execute("select * from containers order by id").fetchall(); c.close()
    dates = sorted({o["date"] for o in orders if o["status"] == "paid"})
    board = "<table class=t><tr><th>Date</th>" + "".join(f"<th>Driver {i} / Truck {i}</th>" for i in (1,2,3)) + "</tr>"
    for dt in dates:
        board += f"<tr><td>{dt}</td>" + "".join("<td>" + "<br>".join(f"<a href='/po/{o['id']}'>{o['id']}</a> {LABEL[o['product']]} {o['size']}' · {esc(o['address'])}" for o in orders if o["date"]==dt and o["driver"]==i and o["status"]=="paid") + "</td>" for i in (1,2,3)) + "</tr>"
    board += "</table>"
    ol = "<table class=t><tr><th>Order</th><th>Customer</th><th>Product</th><th>Status</th><th>Box</th><th>Driver</th><th>PO</th></tr>" + "".join(
        f"<tr><td>{o['id']}</td><td>{esc(o['name'])}</td><td>{LABEL[o['product']]} {o['size']}'</td><td>{o['status']}</td><td>{esc(o['container_id'] or '')}</td><td>{o['driver'] or ''}</td><td>{'<a href=/po/'+o['id']+'>PO</a>' if o['status']=='paid' else '<a href=/pay/'+o['id']+'>pay link</a>'}</td></tr>" for o in orders) + "</table>"
    inv = "<table class=t><tr><th>ID</th><th>Size</th><th>Status</th><th>Order</th></tr>" + "".join(
        f"<tr><td>{b['id']}</td><td>{b['size']}'</td><td><span class='pill {b['status']}'>{b['status']}</span></td><td>{esc(b['order_id'] or '')}</td></tr>" for b in boxes) + "</table>"
    return page("Admin", f"""<div class=eyebrow>Back office</div><h2>Dispatch board</h2>{board}
<p class=sub><a href=/admin/outbox>Notification outbox</a> · <a href=/admin/settings>Driver settings &amp; PINs</a></p>
<p class=sub>Driver views (admin): <a href=/admin/driver/1>Driver 1</a> · <a href=/admin/driver/2>Driver 2</a> · <a href=/admin/driver/3>Driver 3</a></p>
<h2 style="margin-top:48px">Orders</h2>{ol}<h2 style="margin-top:48px">Container inventory</h2>{inv}""", "<a href='/admin'>Admin</a>")

def driver_page(n, po_prefix='/po/'):
    c = db(); name = setting(c, f'driver{n}_name', f'Driver {n}'); jobs = c.execute("select * from orders where driver=? and status='paid' order by date", (n,)).fetchall(); c.close()
    rows = "".join(f"<div class=card style='margin-bottom:16px'><div class=step>{o['date']}</div><h3>{LABEL[o['product']]} — {o['size']}' · {esc(o['container_id'])}</h3><p>{esc(o['address'])}</p><p class=sub>{esc(o['name'])} · {esc(o['phone'])} · Notes: {esc(o['notes']) or '—'}</p><a class=btn href='{po_prefix}{o['id']}'>Work order</a></div>" for o in jobs) or "<p class=sub>No jobs assigned.</p>"
    return page(f"Driver {n}", f"<div class=eyebrow>{esc(name)} · Truck {n}</div><h2>My jobs</h2>{rows}", "<a href='/driver/logout'>Sign out</a>" if po_prefix != '/po/' else "<a href='/admin'>Admin</a>")

def home():
    body = open(os.path.join(ROOT, "static", "index.html")).read()
    return body.replace("{{DEMO_BAR}}", '<div class="demo-bar">DEMO PAYMENT MODE — payments are simulated.</div>' if DEMO else "")

class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def redirect(self, url): self.send(303, "", headers={"Location": url})
    def authed(self):
        a = self.headers.get("Authorization", "")
        if a.startswith("Basic ") and base64.b64decode(a[6:]).decode().split(":", 1)[-1] == ADMIN_PW: return True
        self.send(401, "Auth required", headers={"WWW-Authenticate": 'Basic realm="admin"'}); return False
    def order(self, oid):
        c = db(); o = c.execute("select * from orders where id=?", (oid,)).fetchone(); c.close(); return o
    def driver_session(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "drv": return check_driver_cookie(v)
        return None
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); p = u.path; q = urllib.parse.parse_qs(u.query)
        if p == "/": return self.send(200, home())
        if p == "/checkout": return self.send(200, checkout_form(q))
        if p.startswith("/pay/"):
            o = self.order(p[5:]); return self.send(200, pay_page(o)) if o else self.send(404, "not found")
        if p.startswith("/success/"):
            o = self.order(p[9:]); return self.send(200, page("Thanks", f"<h2>Thank you!</h2><p>Order {esc(o['id'])} is <b>{o['status']}</b>. Scheduled {o['date']} with Driver {o['driver'] or 'TBD'}.</p>"))
        if p == "/api/prices": return self.send(200, json.dumps({"prices": PRICES, "bands": DELIVERY_BANDS}), "application/json")
        if p == "/api/delivery":
            q2 = quote_delivery(q.get("zip", [""])[0]); size = q.get("size", [None])[0]
            if q2.get("miles") is not None: q2["call_for_quote"] = q2.get("fee") is None
            if q2.get("call_for_quote"): q2["message"] = "Over 100 road miles — call for a quote at [phone]. Online payment isn't available for this ZIP."
            if q2.get("fee") is not None:
                q2["buy_delivered"] = {sz: PRICES["buy"][sz]["pickup"] + q2["fee"] for sz in ("20", "40")}
            return self.send(200, json.dumps(q2), "application/json")
        if p in ("/healthz", "/health"):
            try: c = db(); c.execute("select 1"); c.close(); ok = True
            except Exception: ok = False
            return self.send(200 if ok else 503, json.dumps({"ok": ok, "payments": "demo" if DEMO else "stripe", "production": PRODUCTION,
                "sms": notify.sms_enabled(), "email": notify.email_enabled()}), "application/json")
        if p == "/cron/reminders":
            if not CRON_TOKEN or q.get("token", [""])[0] != CRON_TOKEN: return self.send(403, "forbidden")
            return self.send(200, json.dumps({"reminders": send_reminders(force=True)}), "application/json")
        if p in ("/driver", "/driver/"):
            n = self.driver_session()
            return self.send(200, driver_page(n, "/driver/po/")) if n else self.send(200, driver_login_page())
        if p == "/driver/logout":
            return self.send(303, "", headers={"Location": "/driver", "Set-Cookie": "drv=; Path=/; Max-Age=0"})
        if p.startswith("/driver/po/"):
            n = self.driver_session()
            if not n: return self.redirect("/driver")
            o = self.order(p[11:])
            if not o or o["status"] != "paid" or o["driver"] != n: return self.send(404, page("Not found", "<h2>Not one of your jobs.</h2><p><a href=/driver>My jobs</a></p>"))
            return self.send(200, po_html(o).replace("<a href='/admin'>Admin</a>", "<a href='/driver'>My jobs</a>"))
        if p.startswith("/admin") or p.startswith("/po/"):
            if not self.authed(): return
            if p.rstrip("/") == "/admin": return self.send(200, admin_page())
            if p == "/admin/outbox": return self.send(200, outbox_page())
            if p == "/admin/settings": return self.send(200, settings_page())
            if p.startswith("/admin/driver/"): return self.send(200, driver_page(int(p[14:])))
            if p.startswith("/po/"):
                o = self.order(p[4:]); return self.send(200, po_html(o)) if o and o["status"] == "paid" else self.send(404, "PO exists only for paid orders")
        self.send(404, "not found")
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n); p = self.path
        if p == "/driver/login":
            pin = urllib.parse.parse_qs(raw.decode()).get("pin", [""])[0].strip()
            c = db(); match = [i for i in DRIVERS if pin and hmac.compare_digest(setting(c, f"driver{i}_pin"), pin)]; c.close()
            if not match: time.sleep(1); return self.send(401, driver_login_page("Wrong PIN."))
            sec = "; Secure" if BASE_URL.startswith("https") else ""
            return self.send(303, "", headers={"Location": "/driver", "Set-Cookie": f"drv={driver_cookie(match[0])}; Path=/; HttpOnly; SameSite=Lax; Max-Age=1209600{sec}"})
        if p in ("/admin/settings", "/admin/run-reminders"):
            if not self.authed(): return
            if p == "/admin/run-reminders":
                n = send_reminders(force=True); return self.redirect("/admin/outbox")
            f = {k: v[0].strip() for k, v in urllib.parse.parse_qs(raw.decode()).items()}
            pins = [f.get(f"driver{i}_pin", "") for i in DRIVERS]
            if any(not (x.isdigit() and 4 <= len(x) <= 8) for x in pins) or len(set(pins)) != len(pins):
                return self.send(400, settings_page("PINs must be 4–8 digits and unique per driver. Nothing saved."))
            c = db()
            for k, v in f.items():
                if k.startswith("driver") and k.split("_", 1)[-1] in ("name", "phone", "email", "pin"): c.execute("insert or replace into settings values(?,?)", (k, v))
            c.commit(); c.close(); return self.send(200, settings_page("Saved."))
        if p.split("?")[0] == "/api/delivery":
            try: d = json.loads(raw or b"{}")
            except ValueError: d = {}
            if isinstance(d.get("args"), dict): d = d["args"]
            q2 = quote_delivery(str(d.get("zip", "")).strip()[:5])
            if q2.get("miles") is not None: q2["call_for_quote"] = q2.get("fee") is None
            if q2.get("fee") is not None:
                q2["buy_delivered"] = {sz: PRICES["buy"][sz]["pickup"] + q2["fee"] for sz in ("20", "40")}
            return self.send(200, json.dumps(q2), "application/json")
        if p == "/api/voice/order":
            jh = {"Cache-Control": "no-store"}
            if not VOICE_SECRET: return self.send(503, json.dumps({"error": "voice ordering not configured"}), "application/json", jh)
            if not hmac.compare_digest(self.headers.get("X-Voice-Secret", "").encode(), VOICE_SECRET.encode()):
                return self.send(401, json.dumps({"error": "unauthorized"}), "application/json", jh)
            try: d = json.loads(raw or b"{}")
            except ValueError: return self.send(400, json.dumps({"error": "invalid JSON"}), "application/json", jh)
            if isinstance(d.get("args"), dict): d = d["args"]
            code, out = voice_order(d); return self.send(code, json.dumps(out), "application/json", jh)
        if p == "/api/chat":
            d = json.loads(raw or "{}"); return self.send(200, json.dumps({"reply": chat(d.get("sid","anon"), d.get("message",""))}), "application/json")
        if p == "/order":
            f = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
            if f.get("product") != "buy": f["mode"] = "delivered"
            try: return self.redirect("/pay/" + create_order(f))
            except ValueError as e: return self.send(400, page("Delivery quote needed", f"<div class=eyebrow>Call for quote</div><h2>{esc(e)}</h2><p>Online payment isn't available for this delivery. Call [phone] or email info@theyardcontainers.com and we'll quote it.</p><p><a class=btn href='/checkout'>Back</a></p>"))
        if p.startswith("/demo/pay/"):
            if not DEMO: return self.send(403, "Demo payments disabled when STRIPE_SECRET_KEY is set")
            oid = p[10:]; mark_paid(oid, "demo_" + uuid.uuid4().hex[:8]); return self.redirect("/success/" + oid)
        if p.startswith("/stripe/session/"):
            o = self.order(p[16:]); s = stripe_checkout(o)
            c = db(); c.execute("update orders set stripe_session=? where id=?", (s["id"], o["id"])); c.commit(); c.close()
            return self.redirect(s["url"])
        if p == "/webhook/stripe":
            if not verify_stripe_sig(raw, self.headers.get("Stripe-Signature", "")):
                logging.warning("rejected Stripe webhook (bad/missing signature)"); return self.send(400, "bad signature")
            ev = json.loads(raw)
            if ev.get("type") in ("checkout.session.completed", "invoice.paid"):
                obj = ev["data"]["object"]; oid = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id") \
                      or ((obj.get("subscription_details") or {}).get("metadata") or {}).get("order_id")
                if oid: mark_paid(oid, obj.get("id"))
            return self.send(200, '{"received":true}', "application/json")
        self.send(404, "not found")
    def log_message(self, *a): pass

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if PRODUCTION:
        problems = [m for ok, m in ((ADMIN_PW != "demo", "ADMIN_PASSWORD"), (STRIPE_KEY, "STRIPE_SECRET_KEY"), (STRIPE_WHSEC, "STRIPE_WEBHOOK_SECRET"),
                    (os.environ.get("SECRET_KEY"), "SECRET_KEY"), (BASE_URL.startswith("https://"), "BASE_URL (https)")) if not ok]
        if problems: raise SystemExit("APP_ENV=production but missing/unsafe: " + ", ".join(problems))
    init_db(); load_zips()
    if REMINDER_LOOP: threading.Thread(target=reminder_loop, daemon=True).start()
    print(f"The Yard Containers prototype on {BASE_URL} — payments: {'DEMO (simulated)' if DEMO else 'Stripe'}; chat: {'LLM + rules' if LLM_KEY else 'rule-based'}")
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
