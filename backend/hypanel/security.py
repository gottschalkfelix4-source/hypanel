import hashlib
import os
import secrets
import time
import json
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
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        row = store.query("SELECT u.id,u.username,u.role FROM tokens t JOIN users u ON t.user_id=u.id "
                          "WHERE t.token=? AND t.expires>? AND u.disabled=0", (digest(token), time.time()), one=True) if scheme.lower() == "bearer" else None
        if not row:
            raise HTTPException(401, "Ungültiger API-Schlüssel")
        return dict(row, csrf="")
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


RIGHTS = ['console.read', 'console.write', 'server.power', 'files.read', 'files.write', 'config.write',
          'mods.write', 'backups.create', 'backups.restore', 'automation.write', 'access.write', 'settings.write', 'players.write']


def rights(user, sid):
    if user['role'] == 'admin':
        return RIGHTS
    row = store.query('SELECT permission FROM access WHERE user_id=? AND server_id=?', (user['id'], sid), one=True)
    if not row:
        return []
    return store.setting('rights:' + sid + ':' + user['id'], RIGHTS if row['permission'] == 'operator' else ['console.read', 'files.read'])


def access(user, sid, write=False, manage=False, right=None):
    server = store.query("SELECT * FROM servers WHERE id=?", (sid,), one=True)
    if not server:
        raise HTTPException(404, "Server nicht gefunden")
    if user["role"] == "admin":
        return server
    row = store.query("SELECT permission FROM access WHERE user_id=? AND server_id=?", (user["id"], sid), one=True)
    required = right or ('files.write' if write else None)
    if not row or manage or (required and required not in rights(user, sid)):
        raise HTTPException(403, "Kein Zugriff auf diesen Server")
    return server


def audit(user, action, sid=None, detail=""):
    store.execute("INSERT INTO audit(at,user,action,server_id,detail) VALUES(?,?,?,?,?)",
                  (time.time(), user["username"] if isinstance(user, dict) else user, action, sid, detail[:1000]))
    # Retain useful history without unbounded database growth.
    store.execute("DELETE FROM audit WHERE id<(SELECT MAX(id)-20000 FROM audit)")


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
