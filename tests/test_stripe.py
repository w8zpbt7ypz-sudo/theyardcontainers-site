"""Run: python3 tests/test_stripe.py  (Stripe is mocked by a local HTTP server; no network, no real keys)"""
import os, sys, json, threading, hmac, hashlib, time, urllib.request, urllib.parse, urllib.error, tempfile, unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
seen = []
class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        body = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        seen.append({"path": self.path, "auth": self.headers["Authorization"], "form": body})
        code = 200 if self.server.ok else 500
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay/cs_test_123"} if self.server.ok else {"error": {}}).encode())
    def log_message(self, *a): pass
mock = HTTPServer(("127.0.0.1", 0), Mock); mock.ok = True
threading.Thread(target=mock.serve_forever, daemon=True).start()
os.environ.update(STRIPE_SECRET_KEY="sk_test_abc", STRIPE_WEBHOOK_SECRET="whsec_test", STRIPE_API_BASE=f"http://127.0.0.1:{mock.server_port}",
                  BASE_URL="https://theyardcontainers.com", DB_PATH=tempfile.mktemp(suffix=".db"), VOICE_SECRET="vs")
for k in ("SMTP_HOST", "SENDGRID_API_KEY", "TWILIO_ACCOUNT_SID"): os.environ.pop(k, None)
import server
server.init_db()
app = HTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=app.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{app.server_port}"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op = urllib.request.build_opener(NoRedirect)
def req(path, data=None, headers=None):
    r = urllib.request.Request(BASE + path, data=data, headers=headers or {})
    try:
        with op.open(r) as x: return x.status, x.read().decode(), dict(x.headers)
    except urllib.error.HTTPError as e: return e.code, e.read().decode(), dict(e.headers)
def new_order(**kw):
    d = dict(product="buy", size="20", mode="pickup", name="Pat", phone="2695550100", email="pat@example.com"); d.update(kw)
    return server.create_order(d)
def event(oid, typ="checkout.session.completed", amount=None, status="paid", sid="cs_test_123", meta=True):
    o = server.db().execute("select * from orders where id=?", (oid,)).fetchone()
    tot = (o["one_time"] + o["monthly"]) * 100 if o else 100
    obj = {"id": sid, "object": "checkout.session", "payment_status": status,
           "amount_total": amount if amount is not None else tot, "client_reference_id": oid}
    if meta: obj["metadata"] = {"order_id": oid}
    return json.dumps({"id": "evt_1", "type": typ, "data": {"object": obj}}).encode()
def sign(payload, secret="whsec_test", t=None):
    t = int(time.time()) if t is None else t
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
def hook(payload, sig, path="/stripe/webhook"):
    return req(path, payload, {"Stripe-Signature": sig} if sig is not None else {})
def count(oid, kind):
    return server.db().execute("select count(*) from outbox where order_id=? and kind=? and channel='email'", (oid, kind)).fetchone()[0]
def status(oid): return server.db().execute("select status from orders where id=?", (oid,)).fetchone()[0]

class T(unittest.TestCase):
    def test_healthz_live(self):
        h = json.loads(req("/healthz")[1])
        self.assertEqual((h["payments"], h["stripe_mode"], h["stripe_webhook_secret"]), ("live", "test", True))
    def test_pay_page_has_stripe_button_not_demo(self):
        o = new_order(); b = req("/pay/" + o)[1]
        self.assertIn("/stripe/session/" + o, b); self.assertNotIn("/demo/pay/", b); self.assertNotIn("DEMO PAYMENT MODE", b)
    def test_demo_pay_disabled(self):
        o = new_order(); self.assertEqual(req("/demo/pay/" + o, b"")[0], 403); self.assertEqual(status(o), "pending")
    def test_session_creation(self):
        o = new_order(email="pat@example.com"); seen.clear()
        c, _, h = req("/stripe/session/" + o, b"")
        self.assertEqual(c, 303); self.assertEqual(h["Location"], "https://checkout.stripe.com/c/pay/cs_test_123")
        f = seen[-1]["form"]; self.assertEqual(seen[-1]["path"], "/v1/checkout/sessions"); self.assertEqual(seen[-1]["auth"], "Bearer sk_test_abc")
        self.assertEqual(f["mode"], ["payment"]); self.assertEqual(f["line_items[0][price_data][unit_amount]"], ["194500"])
        self.assertEqual(f["metadata[order_id]"], [o]); self.assertEqual(f["client_reference_id"], [o])
        self.assertEqual(f["success_url"], [f"https://theyardcontainers.com/success/{o}"]); self.assertEqual(f["cancel_url"], [f"https://theyardcontainers.com/pay/{o}"])
        self.assertEqual(f["payment_intent_data[metadata][order_id]"], [o]); self.assertEqual(f["customer_email"], ["pat@example.com"])
        self.assertEqual(server.db().execute("select stripe_session from orders where id=?", (o,)).fetchone()[0], "cs_test_123")
        self.assertEqual(status(o), "pending")                      # creating a session never marks paid
    def test_session_amount_delivered_includes_fee(self):
        o = new_order(mode="delivered", zip="49048"); row = server.db().execute("select * from orders where id=?", (o,)).fetchone(); seen.clear()
        req("/stripe/session/" + o, b"")
        self.assertEqual(seen[-1]["form"]["line_items[0][price_data][unit_amount]"], [str(row["one_time"] * 100)]); self.assertGreater(row["one_time"], 1945)
    def test_session_no_email_and_unknown_order_and_stripe_error(self):
        o = new_order(email=""); seen.clear(); req("/stripe/session/" + o, b""); self.assertNotIn("customer_email", seen[-1]["form"])
        self.assertEqual(req("/stripe/session/ORD-NOPE", b"")[0], 404)
        mock.ok = False
        try: c, b, _ = req("/stripe/session/" + o, b"")
        finally: mock.ok = True
        self.assertEqual(c, 502); self.assertIn("Nothing was charged", b)
    def test_webhook_marks_paid_and_emails_once(self):
        o = new_order(); p = event(o)
        self.assertEqual(hook(p, sign(p))[0], 200)
        self.assertEqual(status(o), "paid"); self.assertEqual((count(o, "alert_admin"), count(o, "confirm_customer")), (1, 1))
        self.assertEqual(server.db().execute("select stripe_session from orders where id=?", (o,)).fetchone()[0], "cs_test_123")
        for path in ("/stripe/webhook", "/webhook/stripe"):          # Stripe retries / duplicates: no extra emails
            self.assertEqual(hook(p, sign(p), path)[0], 200)
        self.assertEqual((count(o, "alert_admin"), count(o, "confirm_customer")), (1, 1))
    def test_concurrent_duplicates_send_once(self):
        o = new_order(); p = event(o); sig = sign(p)
        ts = [threading.Thread(target=hook, args=(p, sig)) for _ in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(status(o), "paid"); self.assertEqual((count(o, "alert_admin"), count(o, "confirm_customer")), (1, 1))
    def test_bad_signatures_rejected(self):
        o = new_order(); p = event(o)
        for sig in (None, "", "garbage", sign(p, secret="wrong"), sign(p, t=int(time.time()) - 4000)):
            self.assertEqual(hook(p, sig)[0], 400, sig)
        self.assertEqual(hook(p + b" ", sign(p))[0], 400)          # tampered body
        self.assertEqual(status(o), "pending"); self.assertEqual(count(o, "alert_admin"), 0)
    def test_unpaid_or_short_amount_not_marked(self):
        o = new_order(); p = event(o, status="unpaid"); self.assertEqual(hook(p, sign(p))[0], 200); self.assertEqual(status(o), "pending")
        p = event(o, amount=100); self.assertEqual(hook(p, sign(p))[0], 200); self.assertEqual(status(o), "pending")
        p = event(o, typ="checkout.session.async_payment_succeeded"); hook(p, sign(p)); self.assertEqual(status(o), "paid")
    def test_other_events_and_unknown_order_ignored(self):
        o = new_order(); p = event(o, typ="payment_intent.created"); self.assertEqual(hook(p, sign(p))[0], 200); self.assertEqual(status(o), "pending")
        p = event("ORD-NOPE"); self.assertEqual(hook(p, sign(p))[0], 200)
        self.assertEqual(hook(b"not json", sign(b"not json"))[0], 400)
    def test_order_id_from_client_reference_id(self):
        o = new_order(); p = event(o, meta=False); hook(p, sign(p)); self.assertEqual(status(o), "paid")
    def test_paid_order_pay_button_redirects_to_success(self):
        o = new_order(); p = event(o); hook(p, sign(p)); seen.clear()
        c, _, h = req("/stripe/session/" + o, b""); self.assertEqual((c, h["Location"]), (303, "/success/" + o)); self.assertEqual(seen, [])
    def test_success_page_pending_then_paid(self):
        o = new_order(); self.assertIn("confirming your payment", req("/success/" + o)[1])
        p = event(o); hook(p, sign(p)); self.assertIn("is <b>paid</b>", req("/success/" + o)[1])
if __name__ == "__main__": unittest.main(verbosity=2)
