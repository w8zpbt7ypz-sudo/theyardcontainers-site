"""Run: python3 tests/test_sms_optin_alerts.py  (no network; SMTP/IMAP mocked)"""
import os, sys, tempfile, sqlite3, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = tempfile.mktemp(suffix=".db")
# legacy schema without sms_opt_in -> must migrate
c = sqlite3.connect(DB); c.execute("""CREATE TABLE orders(id TEXT PRIMARY KEY, created TEXT, product TEXT, size TEXT, mode TEXT,
  name TEXT, phone TEXT, email TEXT, address TEXT, date TEXT, notes TEXT, one_time INTEGER, monthly INTEGER, status TEXT,
  stripe_session TEXT, container_id TEXT, driver INTEGER, zip TEXT, miles REAL, delivery_fee INTEGER)""")
c.execute("insert into orders(id,product,size,status) values('ORD-OLD','buy','20','pending')"); c.commit(); c.close()
os.environ.update(DB_PATH=DB, VOICE_SECRET="vs")
import server, notify
def q(sql, *a):
    c = server.db(); r = c.execute(sql, a).fetchone()[0]; c.close(); return r
server.init_db(); server.init_db()  # idempotent

class T(unittest.TestCase):
    def test_migration(self):
        self.assertEqual(q("select sms_opt_in from orders where id='ORD-OLD'"), 0)
    def test_form_optin(self):
        base = dict(product="buy", size="20", mode="pickup", name="A", phone="2695550100", email="a@example.com", address="x")
        a = server.create_order(base); b = server.create_order(dict(base, sms_opt_in="1"))
        g = lambda i: q("select sms_opt_in from orders where id=?", i)
        self.assertEqual((g(a), g(b)), (0, 1))
    def test_checkbox_unchecked(self):
        h = server.checkout_form({}); self.assertIn("name=sms_opt_in", h); self.assertNotIn("checked", h.split("sms_opt_in")[1][:40])
        self.assertIn("Consent not required to purchase", h); self.assertIn("Reply STOP", h)
    def test_voice_optin(self):
        with mock.patch.object(server.threading, "Thread"):
            for v, exp in ((None, 0), (False, 0), (True, 1), ("true", 1)):
                d = dict(name="V", phone="2695550101", product="buy", size="40", mode="pickup")
                if v is not None: d["sms_opt_in"] = v
                code, out = server.voice_order(d); self.assertEqual(code, 200, out)
                self.assertEqual(q("select sms_opt_in from orders where id=?", out["order_id"]), exp)
    def test_sms_gated(self):
        c = server.db()
        with mock.patch.object(notify, "sms_enabled", return_value=True), mock.patch.object(notify, "_sms") as s:
            self.assertEqual(notify.send(c, "sms", "+12695550100", "", "hi", "confirm_customer", sms_ok=False), "skipped"); s.assert_not_called()
            self.assertEqual(notify.send(c, "sms", "+12695550100", "", "hi", "confirm_customer", sms_ok=True), "sent"); s.assert_called_once()
        c.commit(); c.close()
    def test_alert_inbox_append(self):
        with mock.patch.multiple(notify, SMTP_HOST="smtp.test", SMTP_USER="admin@theyardcontainers.com", SMTP_PASS="pw", SENDGRID_KEY=""), \
             mock.patch.object(notify.smtplib, "SMTP") as S, mock.patch.object(notify.imaplib, "IMAP4_SSL") as I:
            c = server.db()
            self.assertEqual(notify.send(c, "email", "orders@theyardcontainers.com", "New order", "b", "alert_admin"), "sent")
            im = I.return_value.__enter__.return_value; im.append.assert_called_once(); self.assertEqual(im.append.call_args[0][0], "INBOX")
            sent = S.return_value.__enter__.return_value.send_message.call_args[0][0]
            self.assertIn("orders@theyardcontainers.com", sent["From"]); self.assertTrue(sent["Message-ID"])
            I.reset_mock(); notify.send(c, "email", "cust@example.com", "Confirm", "b", "confirm_customer"); I.assert_not_called()
            I.side_effect = OSError("imap down")
            self.assertEqual(notify.send(c, "email", "orders@theyardcontainers.com", "x", "b", "alert_voice"), "sent")
            c.commit(); c.close()
if __name__ == "__main__": unittest.main()
