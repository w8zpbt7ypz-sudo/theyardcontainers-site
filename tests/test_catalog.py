"""Run: python3 tests/test_catalog.py  (catalog behind CATALOG_LIVE flag)"""
import os, sys, tempfile, json, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("CATALOG_LIVE", None); os.environ.pop("ENABLED_PRODUCTS", None)
os.environ.update(DB_PATH=tempfile.mktemp(suffix=".db"), VOICE_SECRET="vs")
import server
server.init_db()
P = dict(product="buy", mode="pickup", name="A", phone="2695550100", email="a@example.com")

class Catalog(unittest.TestCase):
    def test_catalog_valid(self):
        skus = [i["sku"] for i in server.CATALOG["items"]]
        self.assertEqual(len(skus), len(set(skus))); self.assertGreaterEqual(len(skus), 30)
        for i in server.CATALOG["items"]:
            if i["orderable"]: self.assertTrue(i["pickup"] and i["pickup"] > 1000)
        for v in server.LEGACY.values(): self.assertTrue(server.CAT[v]["orderable"])
    def test_default_off_unchanged(self):
        self.assertFalse(server.CATALOG_LIVE)
        self.assertEqual(server.price_order("buy", "20", "pickup", 0), (1945, 0))
        self.assertEqual(server.price_order("buy", "40", "delivered", 150), (2695, 0))
        with self.assertRaises(ValueError): server.create_order(dict(P, size="40-hc-new"))
        self.assertNotIn("40-hc-new", server.checkout_form({}))
        self.assertEqual(server.catalog_delivered(150), {})
        code, out = server.voice_order(dict(P, size="40-hc-new", zip="49001")); self.assertEqual(code, 400)
    def test_live_on(self):
        with mock.patch.object(server, "CATALOG_LIVE", True):
            oid = server.create_order(dict(P, size="40-hc-new"))
            o = server.db().execute("select * from orders where id=?", (oid,)).fetchone()
            self.assertEqual((o["size"], o["one_time"]), ("40-hc-new", server.CAT["40-hc-new"]["pickup"]))
            o2 = server.db().execute("select * from orders where id=?", (server.create_order(dict(P, size="20")),)).fetchone()
            self.assertEqual(o2["size"], "20-std-wwt")
            server.mark_paid(o2["id"])
            self.assertTrue(server.db().execute("select container_id from orders where id=?", (o2["id"],)).fetchone()[0].startswith("YC-"))
            with self.assertRaisesRegex(ValueError, "call"): server.create_order(dict(P, size="53-hc-new"))
            with self.assertRaisesRegex(ValueError, "call"): server.create_order(dict(P, size="40-hc-sd-new"))
            d = server.catalog_delivered(150)
            self.assertEqual(d["40-hc-new"], server.CAT["40-hc-new"]["pickup"] + 150)
            self.assertNotIn("53-hc-new", d)
            self.assertIn("40-hc-new", server.checkout_form({}))
            with mock.patch.object(server.threading, "Thread"):
                code, out = server.voice_order(dict(P, size="40-hc-new", mode="delivered", zip="49001"))
            self.assertEqual(code, 200, out)
            code, out = server.voice_order(dict(P, size="40-fr-used", zip="49001")); self.assertEqual(out.get("code"), "call_for_quote")
    def test_long_surcharge(self):
        with mock.patch.dict(server.CAT["45-hc-cw"], orderable=True), mock.patch.object(server, "CATALOG_LIVE", True):
            self.assertEqual(server.price_order("buy", "45-hc-cw", "delivered", 150)[0], server.CAT["45-hc-cw"]["pickup"] + 150 + 150)
    def test_preview_page(self):
        h = server.catalog_page(); self.assertIn("PREVIEW ONLY", h); self.assertIn("53&#x27; high cube", h.replace("53' high cube","53&#x27; high cube"))
        self.assertIn("Call for quote", h)

if __name__ == "__main__": unittest.main()
