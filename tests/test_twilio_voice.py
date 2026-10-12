"""Run: python3 tests/test_twilio_voice.py  (mocks Retell; no network)"""
import os, sys, json, threading, base64, hmac, hashlib, urllib.request, urllib.parse, tempfile, unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
seen = {}
class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        seen["path"] = self.path; seen["auth"] = self.headers["Authorization"]
        seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        code = 201 if self.server.ok else 500
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"call_id": "Jabr9TXYYJHfvl6Syypi88rdAHYHmcq6"} if self.server.ok else {"error": "x"}).encode())
    def log_message(self, *a): pass
mock = HTTPServer(("127.0.0.1", 0), Mock); mock.ok = True
threading.Thread(target=mock.serve_forever, daemon=True).start()
os.environ.update(RETELL_API_KEY="key_test", RETELL_API_BASE=f"http://127.0.0.1:{mock.server_port}",
                  TWILIO_AUTH_TOKEN="tok123", DB_PATH=tempfile.mktemp(suffix=".db"))
import server
server.init_db()
app = HTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=app.serve_forever, daemon=True).start()
URL = "https://theyardcontainers.onrender.com/twilio/voice"
P = {"From": "+12695550123", "To": "+18557689273", "CallSid": "CA123"}
def sign(params): 
    d = URL + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(b"tok123", d.encode(), hashlib.sha1).digest()).decode()
def post(params, sig):
    r = urllib.request.Request(f"http://127.0.0.1:{app.server_port}/twilio/voice", data=urllib.parse.urlencode(params).encode(),
        headers={"X-Twilio-Signature": sig, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(r) as x: return x.status, x.read().decode(), x.headers["Content-Type"]
    except urllib.error.HTTPError as e: return e.code, e.read().decode(), ""
class T(unittest.TestCase):
    def test_ok(self):
        mock.ok = True; c, b, ct = post(P, sign(P))
        self.assertEqual(c, 200); self.assertIn("xml", ct)
        self.assertIn("<Dial><Sip>sip:Jabr9TXYYJHfvl6Syypi88rdAHYHmcq6@sip.retellai.com</Sip></Dial>", b)
        self.assertEqual(seen["path"], "/v2/register-phone-call"); self.assertEqual(seen["auth"], "Bearer key_test")
        self.assertEqual(seen["body"]["agent_id"], "agent_99d6cc33d6d94f7e1e462f0be8")
        self.assertEqual((seen["body"]["from_number"], seen["body"]["to_number"], seen["body"]["direction"]), ("+12695550123", "+18557689273", "inbound"))
    def test_bad_sig(self):
        self.assertEqual(post(P, "nope")[0], 403)
    def test_retell_fail(self):
        mock.ok = False; c, b, _ = post(P, sign(P)); mock.ok = True
        self.assertEqual(c, 200); self.assertIn("<Say", b); self.assertIn("call back", b); self.assertNotIn("<Dial>", b)
if __name__ == "__main__": unittest.main(verbosity=2)
