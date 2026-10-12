"""Notifications: Twilio SMS + SMTP/SendGrid email, with an outbox table fallback.
Every message is recorded in the outbox; status = sent | logged (no keys) | failed."""
import os, json, base64, smtplib, ssl, datetime, urllib.request, urllib.parse, logging
from email.message import EmailMessage

log = logging.getLogger("notify")
TW_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TW_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TW_FROM = os.environ.get("TWILIO_FROM_NUMBER", "")          # or TWILIO_MESSAGING_SERVICE_SID
TW_MSID = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")
SENDGRID_KEY = os.environ.get("SENDGRID_API_KEY", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "The Yard Containers <info@theyardcontainers.com>")
DISPATCH_FROM = os.environ.get("DISPATCH_FROM", "The Yard Containers Dispatch <dispatch@theyardcontainers.com>")
DISPATCH_REPLY_TO = os.environ.get("DISPATCH_REPLY_TO", "dispatch@theyardcontainers.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "info@theyardcontainers.com")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "info@theyardcontainers.com")

def sms_enabled(): return bool(TW_SID and TW_TOKEN and (TW_FROM or TW_MSID))
def email_enabled(): return bool(SENDGRID_KEY or SMTP_HOST)

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, channel TEXT,
        to_addr TEXT, subject TEXT, body TEXT, status TEXT, error TEXT, kind TEXT, order_id TEXT)""")

def _sms(to, body):
    data = {"To": to, "Body": body}
    data["MessagingServiceSid" if TW_MSID else "From"] = TW_MSID or TW_FROM
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Messages.json",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(f"{TW_SID}:{TW_TOKEN}".encode()).decode()})
    urllib.request.urlopen(req, timeout=20).read()

def _email(to, subject, body, sender=None, reply_to=None):
    MAIL_FROM_ = sender or MAIL_FROM
    if SENDGRID_KEY:
        name, addr = (MAIL_FROM_.split("<")[0].strip(), MAIL_FROM_.split("<")[1].rstrip(">")) if "<" in MAIL_FROM_ else ("", MAIL_FROM_)
        payload = {"personalizations": [{"to": [{"email": to}]}], "from": {"email": addr, "name": name},
                   "subject": subject, "content": [{"type": "text/plain", "value": body}]}
        if reply_to: payload["reply_to"] = {"email": reply_to}
        req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + SENDGRID_KEY, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read(); return
    m = EmailMessage(); m["From"] = MAIL_FROM_; m["To"] = to;
    if reply_to: m["Reply-To"] = reply_to
    m["Subject"] = subject; m.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        if SMTP_USER: s.login(SMTP_USER, SMTP_PASS)
        s.send_message(m)

def send(c, channel, to, subject, body, kind="", order_id=None, sender=None, reply_to=None):
    """channel: 'sms' | 'email'. Never raises; returns the outbox status."""
    status, err = "logged", None
    if not to or "[" in to:
        status, err = "logged", "no/placeholder address"
    elif (channel == "sms" and sms_enabled()) or (channel == "email" and email_enabled()):
        try:
            (_sms(to, body) if channel == "sms" else _email(to, subject, body, sender, reply_to)); status = "sent"
        except Exception as e:
            status, err = "failed", str(e)[:300]; log.warning("notify %s to %s failed: %s", channel, to, e)
    c.execute("insert into outbox(created,channel,to_addr,subject,body,status,error,kind,order_id) values(?,?,?,?,?,?,?,?,?)",
              (datetime.datetime.now().isoformat(timespec="seconds"), channel, to, subject, body, status, err, kind, order_id))
    return status
