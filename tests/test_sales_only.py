"""Run: python3 tests/test_sales_only.py  (default ENABLED_PRODUCTS=buy)"""
import os, sys, tempfile, json, unittest, importlib
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("ENABLED_PRODUCTS", None)
os.environ.update(DB_PATH=tempfile.mktemp(suffix=".db"), VOICE_SECRET="vs")
import server
server.init_db()

class SalesOnly(unittest.TestCase):
    def test_flag_default(self):
        self.assertEqual(server.ENABLED, ["buy"]); self.assertFalse(server.RENTALS_ON)
    def test_home_has_no_rentals(self):
        h = server.home().lower()
        for bad in ("product=rent", "product=rto", "product=lease", "/ month", "rent-to-own", 'href="#rent"', "<!--if:"):
            self.assertNotIn(bad, h)
        self.assertIn("product=buy", h); self.assertIn("coming soon", h); self.assertIn("$2,545", h)
    def test_checkout_buy_only(self):
        h = server.checkout_form({"product": ["rent"]})
        self.assertIn("<option value=buy selected>", h)
        for k in ("rent", "rto", "lease"): self.assertNotIn(f"<option value={k}", h)
        self.assertNotIn("/mo", server.price_sheet()); self.assertNotIn("Rent", server.page("x", ""))
    def test_web_order_rejected(self):
        for p in ("rent", "rto", "lease", ""):
            with self.assertRaisesRegex(ValueError, "coming soon"):
                server.create_order(dict(product=p, size="20", zip="49001", name="A", phone="2695550100"))
        oid = server.create_order(dict(product="buy", size="40", mode="pickup", name="A", phone="2695550100", email="a@example.com"))
        self.assertTrue(oid.startswith("ORD-"))
    def test_voice_rejected(self):
        code, out = server.voice_order(dict(product="rent", size="20", zip="49001", name="A", phone="2695550100"))
        self.assertEqual((code, out["error"]), (400, "product_not_offered")); self.assertIn("coming soon", out["message"])
        with mock.patch.object(server.threading, "Thread"):
            code, out = server.voice_order(dict(product="buy", size="40", zip="49001", name="A", phone="2695550100"))
        self.assertEqual(code, 200, out)
    def test_delivery_quote(self):
        q = server.quote_delivery("49001"); self.assertIsNotNone(q.get("fee"))
    def test_chat_rent_redirects(self):
        r = server.chat("s1", "I want to rent storage"); self.assertIn("coming soon", r)
        r = server.chat("s1", "buy"); self.assertIn("20' or 40'", r)

class RentalsReenabled(unittest.TestCase):
    def test_flag_on(self):
        with mock.patch.dict(os.environ, {"ENABLED_PRODUCTS": "buy,rent,rto,lease"}):
            s = importlib.reload(server)
            try:
                self.assertTrue(s.RENTALS_ON); h = s.home()
                self.assertIn("product=rent", h); self.assertIn("rent-to-own", h.lower())
                self.assertIn("<option value=rto", s.checkout_form({}))
                self.assertIn("/mo", s.price_sheet())
            finally:
                os.environ.pop("ENABLED_PRODUCTS"); importlib.reload(s)

if __name__ == "__main__": unittest.main()
