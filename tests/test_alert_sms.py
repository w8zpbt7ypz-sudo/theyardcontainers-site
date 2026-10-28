"""Run: python3 tests/test_alert_sms.py  (no network)"""
import os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(DB_PATH=tempfile.mktemp(suffix=".db"), VOICE_SECRET="vs", ALERT_PHONE="+19294200093")
import server, notify
server.init_db()

class Sync:
    def __init__(self, target, args=(), daemon=None): self.t, self.a = target, args
    def start(self): self.t(*self.a)

class T(unittest.TestCase):
    def run_order(self, fn):
        with mock.patch.object(server.threading, "Thread", Sync), mock.patch.object(notify, "sms_enabled", return_value=True), \
             mock.patch.object(notify, "_sms") as s, mock.patch.object(notify, "_email"), mock.patch.object(notify, "email_enabled", return_value=True):
            fn(); return [x for x in s.call_args_list if x.args[0] == "+19294200093"]
    def test_web(self):
        calls = self.run_order(lambda: server.create_order(dict(product="buy", size="20", mode="pickup", name="Ann Lee", phone="2695550100", address="1 Main")))
        self.assertEqual(len(calls), 1); body = calls[0].args[1]
        for k in ("New order ORD-", "Ann Lee", "2695550100", "Total $", "/admin"): self.assertIn(k, body)
        self.assertLessEqual(len(body), 320)
    def test_voice(self):
        calls = self.run_order(lambda: server.voice_order(dict(name="Vic", phone="2695550101", product="buy", size="40", mode="pickup")))
        self.assertEqual(len(calls), 1)
        c = server.db(); r = c.execute("select kind,status from outbox where kind='alert_new_order_sms' order by id desc").fetchone(); c.close()
        self.assertEqual(tuple(r), ("alert_new_order_sms", "sent"))
    def test_long_truncated(self):
        o = dict(id="ORD-X", product="buy", size="20", mode="delivered", name="N"*200, phone="1", address="A"*200, zip="49048", one_time=1, monthly=0)
        b = server.new_order_sms_text(o); self.assertLessEqual(len(b), 320); self.assertTrue(b.endswith("/admin"))

if __name__ == "__main__": unittest.main()
