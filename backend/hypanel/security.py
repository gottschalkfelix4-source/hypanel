import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request
from . import store

hasher = PasswordHasher()
failures = defaultdict(deque)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(value):
    if len(value) < 12 or len(value) > 256:
        raise HTTPException(400, "Passwort muss 12 bis 256 Zeichen lang sein")
    return hasher.hash(value)


def verify_password(encoded, value):
    try:
        return hasher.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


def current_user(request: Request):
    token = request.cookies.get("hypanel_session", "")
    row = store.query("SELECT u.id,u.username,u.role,s.csrf FROM sessions s JOIN users u ON s.user_id=u.id "
                      "WHERE s.token=? AND s.expires>? AND u.disabled=0", (digest(token), time.time()), one=True)
    if not row:
        raise HTTPException(401, "Bitte anmelden")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"]):
            raise HTTPException(403, "CSRF-Prüfung fehlgeschlagen")
    return row


def admin(user):
    if user["role"] != "admin":
        raise HTTPException(403, "Administratorrechte erforderlich")


def access(user, sid, write=False, manage=False):
    server = store.query("SELECT * FROM servers WHERE id=?", (sid,), one=True)
    if not server:
        raise HTTPException(404, "Server nicht gefunden")
    if user["role"] == "admin":
        return server
    row = store.query("SELECT permission FROM access WHERE user_id=? AND server_id=?", (user["id"], sid), one=True)
    if not row or manage or (write and row["permission"] != "operator"):
        raise HTTPException(403, "Kein Zugriff auf diesen Server")
    return server


def audit(user, action, sid=None, detail=""):
    store.execute("INSERT INTO audit(at,user,action,server_id,detail) VALUES(?,?,?,?,?)",
                  (time.time(), user["username"] if isinstance(user, dict) else user, action, sid, detail[:1000]))


def check_origin(request):
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = os.getenv("HYPANEL_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    if origin != allowed:
        raise HTTPException(403, "Anfrage von einer nicht erlaubten Herkunft")


def throttle(key):
    now = time.time()
    queue = failures[key]
    while queue and queue[0] < now - 300:
        queue.popleft()
    if len(queue) >= 10:
        raise HTTPException(429, "Zu viele Anmeldeversuche. Nach fünf Minuten erneut versuchen.")
    queue.append(now)
