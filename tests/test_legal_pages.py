"""Run: python3 tests/test_legal_pages.py"""
import os, sys, tempfile, unittest, threading, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(DB_PATH=tempfile.mktemp(suffix=".db"))
import server
server.init_db()
from http.server import ThreadingHTTPServer

class Legal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()
    def get(self, p):
        r = urllib.request.urlopen(self.base + p); return r.status, r.read().decode()
    def test_pages(self):
        for p in ("/privacy", "/terms"):
            code, h = self.get(p)
            self.assertEqual(code, 200)
            for s in ("STOP", "HELP", "sole proprietorship"): self.assertIn(s, h)
            self.assertNotIn("LLC", h)
    def test_privacy_carrier_line(self):
        self.assertIn("Text messaging originator opt-in data and consent will not be shared with any third parties.", self.get("/privacy")[1])
    def test_links(self):
        for p in ("/", "/checkout"):
            h = self.get(p)[1]; self.assertIn("/privacy", h); self.assertIn("/terms", h)

    def test_twilio_exact_phrases(self):
        import re
        _, pv = self.get("/privacy")
        self.assertIn("All the above categories exclude text messaging originator opt-in data and consent; this information won't be shared with any third parties.", pv)
        _, tm = self.get("/terms"); self.assertIn("Message and data rates may apply", tm)
        _, co = self.get("/checkout"); self.assertIn("Message and data rates may apply", co)
        box = re.search(r"<input[^>]*name=sms_opt_in[^>]*>", co).group(0)
        self.assertNotIn("checked", box)

if __name__ == "__main__": unittest.main()
