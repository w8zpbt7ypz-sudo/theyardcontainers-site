"""Run: python3 tests/test_multi_delivery.py"""
import os, sys, tempfile, sqlite3, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(DB_PATH=tempfile.mktemp(suffix=".db"), VOICE_SECRET="vs",
                  DEPOSIT_PERCENT="25", INSTALLMENT_COUNT="3", FUEL_PERCENT="0", WEATHER_FEE="0")
for k in ("STRIPE_SECRET_KEY", "SMTP_HOST", "SENDGRID_API_KEY", "ALERT_PHONE"): os.environ.pop(k, None)
import server
server.init_db()

class MultiDelivery(unittest.TestCase):
    def test_multi_line_distinct_dates(self):
        oid = server.create_order(dict(
            product="buy", mode="pickup", name="A", phone="2695550100", email="a@example.com",
            payment_plan="per_delivery",
            lines=[{"size": "20", "date": "2026-12-01"}, {"size": "40", "date": "2026-12-15"}],
        ))
        lines = server.order_lines(oid)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["delivery_date"], "2026-12-01")
        self.assertEqual(lines[1]["delivery_date"], "2026-12-15")
        self.assertEqual({lines[0]["size"], lines[1]["size"]}, {"20", "40"})

    def test_per_line_charges_and_deposit_math(self):
        oid = server.create_order(dict(
            product="buy", mode="delivered", zip="49048", name="A", phone="2695550100", email="a@example.com",
            payment_plan="per_delivery",
            lines=[{"size": "20", "date": "2026-11-20"}, {"size": "20", "date": "2026-11-25"}],
        ))
        o = server.db().execute("select * from orders where id=?", (oid,)).fetchone()
        lines = server.order_lines(oid)
        self.assertEqual(sum(l["line_total"] for l in lines), o["one_time"])
        for l in lines:
            self.assertEqual(l["fuel_fee"], 0)
            self.assertEqual(l["weather_fee"], 0)
            self.assertEqual(l["line_total"], l["container_price"] + l["transport_fee"] + l["fuel_fee"] + l["weather_fee"])
            self.assertGreater(l["transport_fee"], 0)
        self.assertEqual(o["deposit"], int(round(o["one_time"] * 25 / 100)))
        self.assertEqual(o["balance"], o["one_time"] - o["deposit"])
        self.assertEqual(server.amount_due_now(o), o["deposit"])

    def test_per_delivery_dispatch_gate(self):
        oid = server.create_order(dict(
            product="buy", mode="pickup", name="A", phone="2695550100", email="a@example.com",
            payment_plan="per_delivery", size="20", date="2026-12-01",
        ))
        server.mark_paid(oid, "demo_x")
        ln = server.order_lines(oid)[0]
        ok, msg = server.dispatch_delivery_line(ln["id"])
        self.assertFalse(ok); self.assertIn("charge", msg.lower())
        ok, msg = server.charge_delivery_line(ln["id"], demo=True)
        self.assertTrue(ok)
        ok, msg = server.dispatch_delivery_line(ln["id"])
        self.assertTrue(ok)
        st = server.db().execute("select status from delivery_lines where id=?", (ln["id"],)).fetchone()[0]
        self.assertEqual(st, "dispatched")

    def test_installment_schedule_sums_to_balance(self):
        oid = server.create_order(dict(
            product="buy", mode="pickup", name="A", phone="2695550100", email="a@example.com",
            payment_plan="installments",
            lines=[{"size": "40", "date": "2026-12-01"}, {"size": "20", "date": "2026-12-08"}],
        ))
        o = server.db().execute("select * from orders where id=?", (oid,)).fetchone()
        inst = server.db().execute("select * from installments where order_id=? order by seq", (oid,)).fetchall()
        self.assertEqual(len(inst), 3)
        self.assertEqual(sum(i["amount"] for i in inst), o["balance"])
        server.mark_paid(oid, "demo_y")
        for i in inst:
            ok, _ = server.charge_installment(i["id"], demo=True)
            self.assertTrue(ok)
        bal = server.db().execute("select balance from orders where id=?", (oid,)).fetchone()[0]
        self.assertEqual(bal, 0)

    def test_legacy_migration(self):
        db = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(db)
        c.execute("""CREATE TABLE orders(id TEXT PRIMARY KEY, created TEXT, product TEXT, size TEXT, mode TEXT,
          name TEXT, phone TEXT, email TEXT, address TEXT, date TEXT, notes TEXT, one_time INTEGER, monthly INTEGER, status TEXT,
          stripe_session TEXT, container_id TEXT, driver INTEGER, zip TEXT, miles REAL, delivery_fee INTEGER)""")
        c.execute("""insert into orders(id,created,product,size,mode,name,phone,email,address,date,notes,one_time,monthly,status,
          stripe_session,container_id,driver,zip,miles,delivery_fee)
          values('ORD-LEG','2026-01-01','buy','20','pickup','Pat','2695550199','p@example.com','1 Main','2026-01-10','',1945,0,'pending',NULL,NULL,NULL,'',NULL,0)""")
        c.commit(); c.close()
        os.environ["DB_PATH"] = db
        import importlib
        s = importlib.reload(server)
        s.DB = db
        s.init_db()
        n = s.db().execute("select count(*) from delivery_lines where order_id='ORD-LEG'").fetchone()[0]
        self.assertEqual(n, 1)
        row = s.db().execute("select deposit, balance, payment_plan, one_time from orders where id='ORD-LEG'").fetchone()
        self.assertEqual(row["one_time"], 1945)
        self.assertEqual(row["deposit"], int(round(1945 * 25 / 100)))
        self.assertEqual(row["balance"], 1945 - row["deposit"])
        self.assertEqual(row["payment_plan"], "per_delivery")
        # restore module DB for other tests in this process
        importlib.reload(server)

    def test_voice_single_payload_backward_compat(self):
        with mock.patch.object(server.threading, "Thread"):
            code, out = server.voice_order(dict(name="Vic", phone="2695550101", product="buy", size="40", mode="pickup"))
        self.assertEqual(code, 200, out)
        self.assertIn("order_id", out)
        self.assertEqual(len(out.get("lines") or []), 1)
        self.assertEqual(out["totals"]["deposit"], int(round(2545 * 25 / 100)))

    def test_checkout_keeps_sms_unchecked_and_phrase(self):
        h = server.checkout_form({})
        self.assertIn("name=sms_opt_in", h)
        self.assertNotIn("checked", h.split("sms_opt_in")[1][:40])
        self.assertIn("Message and data rates may apply", h)
        self.assertIn("Add another container", h)
        self.assertIn("payment_plan", h)

if __name__ == "__main__":
    unittest.main(verbosity=2)
