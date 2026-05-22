"""Private email distribution of the daily landscape digest.

Composes a digest (the briefing plus a POC-leads list) and sends it to the
recipients in `data/email_recipients.txt` — a gitignored file the user
controls. SMTP credentials are read from environment variables only.

Both inputs are strictly opt-in: if the recipient file is missing or empty,
or if SMTP credentials are not set, the routine is a graceful no-op so the
project runs fully without any of it. Credentials and addresses are never
written to disk or logs by this module.

Environment variables:
    AIL_SMTP_HOST       SMTP server hostname
    AIL_SMTP_PORT       SMTP port (default 587)
    AIL_SMTP_USER       SMTP username
    AIL_SMTP_PASSWORD   SMTP password / app password
    AIL_SMTP_FROM       From address (e.g. you@example.org)
"""

import os
import pathlib
import smtplib
from email.message import EmailMessage

from . import briefing


class EmailError(Exception):
    """Raised when the digest cannot be sent."""


def load_recipients(path):
    """Read recipient email addresses from `path`, ignoring blanks/comments."""
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


def smtp_config():
    """SMTP settings from the environment. Missing fields stay None."""
    return {
        "host": os.environ.get("AIL_SMTP_HOST"),
        "port": int(os.environ.get("AIL_SMTP_PORT", "587") or 587),
        "user": os.environ.get("AIL_SMTP_USER"),
        "password": os.environ.get("AIL_SMTP_PASSWORD"),
        "sender": os.environ.get("AIL_SMTP_FROM"),
    }


def _poc_leads(kg_store):
    """Top recent person POCs — people active in the graph, ranked by recency
    then mentions. Returns at most 10."""
    persons = [n for n in kg_store.nodes() if n["type"] == "person"]
    persons.sort(
        key=lambda n: (n.get("last_seen") or "", n["mention_count"]),
        reverse=True,
    )
    return persons[:10]


def build_digest(documents, kg_store, days=7):
    """Compose the digest body text from the briefing and POC leads."""
    body = briefing.render_briefing(
        briefing.build_briefing(documents, kg_store, days=days)
    )
    leads = _poc_leads(kg_store)
    body += "\n\nPOC LEADS\n"
    if leads:
        for n in leads:
            body += "  %-28s  %d mentions  (last seen %s)\n" % (
                n["canonical_name"][:28], n["mention_count"],
                n.get("last_seen") or "?",
            )
    else:
        body += "  (none)\n"
    return body


def _default_send(smtp, message):
    """Send `message` via SMTP using STARTTLS."""
    with smtplib.SMTP(smtp["host"], smtp["port"]) as server:
        server.starttls()
        server.login(smtp["user"], smtp["password"])
        server.send_message(message)


def send_digest(recipients, body, subject, smtp, sender_fn=None):
    """Send a composed digest. `sender_fn` is injectable for tests."""
    if not recipients:
        raise EmailError("no recipients configured")
    missing = [k for k in ("host", "user", "password", "sender")
               if not smtp.get(k)]
    if missing:
        raise EmailError(
            "SMTP credentials incomplete; set AIL_SMTP_" +
            ",".join(m.upper() for m in missing)
        )
    message = EmailMessage()
    message["From"] = smtp["sender"]
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    (sender_fn or _default_send)(smtp, message)


def daily_digest(documents, kg_store, recipients_path, days=7,
                  sender_fn=None):
    """Build and send the daily digest, opt-in. Returns a status dict.

    `sender_fn(smtp, message)` is injectable so tests do not touch a network.
    """
    recipients = load_recipients(recipients_path)
    if not recipients:
        return {"sent": False, "reason": "no recipients configured"}
    smtp = smtp_config()
    missing = [k for k in ("host", "user", "password", "sender")
               if not smtp.get(k)]
    if missing:
        return {"sent": False, "reason": "SMTP env vars not configured"}
    body = build_digest(documents, kg_store, days=days)
    subject = "AI Landscape Daily Digest"
    try:
        send_digest(recipients, body, subject, smtp, sender_fn=sender_fn)
    except EmailError as exc:
        return {"sent": False, "reason": str(exc)}
    return {"sent": True, "recipients": len(recipients)}
