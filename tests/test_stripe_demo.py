"""Run: python3 tests/test_stripe_demo.py  (no STRIPE_SECRET_KEY: DEMO behavior unchanged)"""
import os, sys, json, threading, urllib.request, urllib.error, tempfile, unittest
from http.server import HTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SMTP_HOST", "SENDGRID_API_KEY"): os.environ.pop(k, None)
os.environ.update(DB_PATH=tempfile.mktemp(suffix=".db"), VOICE_SECRET="vs")
import server
server.init_db()
app = HTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=app.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{app.server_port}"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op = urllib.request.build_opener(NoRedirect)
def req(path, data=None, headers=None):
    try:
        with op.open(urllib.request.Request(BASE + path, data=data, headers=headers or {})) as x: return x.status, x.read().decode(), dict(x.headers)
    except urllib.error.HTTPError as e: return e.code, e.read().decode(), dict(e.headers)
class T(unittest.TestCase):
    def test_demo_flow_unchanged(self):
        self.assertEqual(json.loads(req("/healthz")[1])["payments"], "demo")
        o = server.create_order(dict(product="buy", size="20", mode="pickup", name="A", phone="2695550100", email="a@example.com"))
        b = req("/pay/" + o)[1]; self.assertIn("/demo/pay/" + o, b); self.assertIn("DEMO PAYMENT MODE", b)
        c, _, h = req("/demo/pay/" + o, b""); self.assertEqual((c, h["Location"]), (303, "/success/" + o))
        st = server.db().execute("select status, stripe_session from orders where id=?", (o,)).fetchone()
        self.assertEqual(st[0], "paid"); self.assertTrue(st[1].startswith("demo_"))
        n = lambda k: server.db().execute("select count(*) from outbox where order_id=? and kind=? and channel='email'", (o, k)).fetchone()[0]
        self.assertEqual((n("alert_admin"), n("confirm_customer")), (1, 1))
        req("/demo/pay/" + o, b""); self.assertEqual((n("alert_admin"), n("confirm_customer")), (1, 1))   # clicking twice: no duplicate emails
    def test_stripe_endpoints_inert_in_demo(self):
        o = server.create_order(dict(product="buy", size="20", mode="pickup", name="A", phone="2695550100", email="a@example.com"))
        self.assertEqual(req("/stripe/session/" + o, b"")[0], 403)
        self.assertEqual(req("/stripe/webhook", b'{"type":"checkout.session.completed"}')[0], 400)   # no webhook secret: nothing accepted
if __name__ == "__main__": unittest.main(verbosity=2)
