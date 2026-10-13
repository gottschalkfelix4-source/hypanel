import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import psutil
from docker.errors import DockerException
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import store, runtime, operations, jobs, __version__
from . import metrics as monitoring
from .features import router as features_router
from .files import root, safe_path, atomic_write, extract_zip, owned_tree, PROTECTED
from .security import current_user, admin, access, audit, check_origin, hash_password, verify_password, throttle, digest, rights, RIGHTS
from .limits import UploadLimitMiddleware

User = Annotated[dict, Depends(current_user)]
VERSION = __version__
stop_event = threading.Event()
SETUP_LOCK = threading.Lock()
UPLOAD_LIMIT = int(os.getenv("HYPANEL_UPLOAD_LIMIT_GB", "16")) * 1024**3


@asynccontextmanager
async def lifespan(app):
    store.init()
    jobs.recover()
    if not store.query("SELECT id FROM users LIMIT 1"):
        key = store.DATA / "setup.key"
        if not key.exists():
            key.write_text(secrets.token_urlsafe(32), encoding="utf-8")
            key.chmod(0o600)
    stop_event.clear()
    thread = threading.Thread(target=operations.scheduler, args=(stop_event,), daemon=True)
    thread.start()
    metrics_thread = threading.Thread(target=monitoring.collector, args=(stop_event,), daemon=True)
    metrics_thread.start()
    sftp_thread = None
    if os.getenv('HYPANEL_SFTP_ENABLED', 'true').lower() == 'true':
        from . import sftp
        sftp_thread = threading.Thread(target=sftp.listen, args=(stop_event,), daemon=True)
        sftp_thread.start()
    for server in store.query("SELECT * FROM servers WHERE autostart=1"):
        try:
            if not runtime.active(server["id"]):
                runtime.launch(server)
        except Exception:
            audit("system", "autostart.failed", server["id"])
    yield
    stop_event.set()
    thread.join(timeout=2)
    metrics_thread.join(timeout=2)
    if sftp_thread:
        sftp_thread.join(timeout=2)


app = FastAPI(title="HyPanel", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(UploadLimitMiddleware)


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        try:
            check_origin(request)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(DockerException)
async def docker_error(request, error):
    return JSONResponse({"detail": "Docker-Operation fehlgeschlagen. Image, Speicher, Port und Docker-Verbindung prüfen."}, status_code=503)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=256)


class Setup(Credentials):
    setup_key: str


@app.get("/api/health")
def health():
    store.query("SELECT 1")
    return {"status": "ok", "version": VERSION}


@app.get("/api/setup")
def setup_state():
    return {"required": not bool(store.query("SELECT id FROM users LIMIT 1"))}


@app.post("/api/setup")
def setup(body: Setup, request: Request):
    throttle("setup:" + request.client.host)
    with SETUP_LOCK:
        if store.query("SELECT id FROM users LIMIT 1"):
            raise HTTPException(409, "Einrichtung ist bereits abgeschlossen")
        key = store.DATA / "setup.key"
        if not key.exists() or not secrets.compare_digest(body.setup_key, key.read_text().strip()):
            raise HTTPException(403, "Ungültiger Einrichtungsschlüssel")
        encoded = hash_password(body.password)
        store.execute("INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)",
                      (uuid.uuid4().hex, body.username, encoded, "admin"))
        key.unlink(missing_ok=True)
        audit(body.username, "panel.setup")
    return {"ok": True}


@app.post("/api/login")
def login(body: Credentials, request: Request, response: Response):
    throttle("login:" + request.client.host)
    user = store.query("SELECT * FROM users WHERE username=? AND disabled=0", (body.username,), one=True)
    if not user or not verify_password(user["password"], body.password):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    store.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
    store.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), user["id"], csrf, time.time() + 86400))
    secure = os.getenv("HYPANEL_COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie("hypanel_session", token, httponly=True, secure=secure, samesite="strict", max_age=86400, path="/")
    audit(user, "auth.login")
    return {"id": user["id"], "username": user["username"], "role": user["role"], "csrf": csrf}


@app.get("/api/me")
def me(user: User):
    return user


@app.post("/api/logout")
def logout(request: Request, response: Response, user: User):
    store.execute("DELETE FROM sessions WHERE token=?", (digest(request.cookies.get("hypanel_session", "")),))
    response.delete_cookie("hypanel_session")
    return {"ok": True}


class PasswordChange(BaseModel):
    current: str
    password: str


@app.post("/api/me/password")
def change_password(body: PasswordChange, user: User):
    row = store.query("SELECT password FROM users WHERE id=?", (user["id"],), one=True)
    if not verify_password(row["password"], body.current):
        raise HTTPException(403, "Aktuelles Passwort falsch")
    store.execute("UPDATE users SET password=? WHERE id=?", (hash_password(body.password), user["id"]))
    store.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    store.execute("DELETE FROM tokens WHERE user_id=?", (user["id"],))
    audit(user, "auth.password_changed")
    return {"ok": True}


class ServerSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    port: int = Field(default=5520, ge=1024, le=65535)
    memory_mb: int = Field(default=4096, ge=4096, le=262144)
    cpus: float = Field(default=2, ge=0.5, le=128)
    autostart: bool = False
    autorestart: bool = False
    patchline: Literal["release", "pre-release"] = "release"


@app.get("/api/servers")
def list_servers(user: User):
    rows = store.query("SELECT * FROM servers ORDER BY created") if user["role"] == "admin" else store.query(
        "SELECT s.* FROM servers s JOIN access a ON s.id=a.server_id WHERE a.user_id=? ORDER BY created", (user["id"],))
    return [server_state(s, user) for s in rows]


def server_state(server, user, metrics=False):
    result = runtime.state(server, metrics=metrics)
    permission = "admin" if user["role"] == "admin" else store.query(
        "SELECT permission FROM access WHERE server_id=? AND user_id=?", (server["id"], user["id"]), one=True)["permission"]
    data = monitoring.telemetry(server['id'], result['status'] == 'running')
    last = store.query('SELECT * FROM metrics WHERE server_id=? ORDER BY at DESC LIMIT 1', (server['id'],), one=True)
    if last and time.time() - last['at'] > 30:
        last = None
    return dict(result, permission=permission, rights=rights(user, server['id']), last_metric=last, ready=data['available'])


def insert_server(body):
    if store.query("SELECT id FROM servers WHERE port=?", (body.port,)):
        raise HTTPException(409, "UDP-Port ist bereits einem Server zugewiesen")
    sid = uuid.uuid4().hex
    directory = root(sid)
    (directory / "Server/mods").mkdir(parents=True)
    (directory / "Server/universe/worlds").mkdir(parents=True)
    machine_id = store.DATA / "identities" / sid
    machine_id.write_text(uuid.uuid4().hex + "\n")
    machine_id.chmod(0o444)
    owned_tree(directory)
    store.execute("INSERT INTO servers VALUES(?,?,?,?,?,?,?,?,?)", (sid, body.name, body.port, body.memory_mb, body.cpus,
                  body.autostart, body.autorestart, body.patchline, time.time()))
    return store.query("SELECT * FROM servers WHERE id=?", (sid,), one=True)


@app.post("/api/servers", status_code=201)
def create_server(body: ServerSpec, user: User):
    admin(user)
    server = insert_server(body)
    audit(user, "server.create", server["id"], body.name)
    return server_state(server, user)


@app.get("/api/servers/{sid}")
def get_server(sid: str, user: User):
    return server_state(access(user, sid), user, metrics=True)


@app.put("/api/servers/{sid}")
def edit_server(sid: str, body: ServerSpec, user: User):
    original = access(user, sid, right="settings.write")
    if user["role"] != "admin" and any(getattr(body, key) != original[key] for key in ('port','memory_mb','cpus','patchline')):
        raise HTTPException(403, "Ports, Ressourcen und Update-Kanal dürfen nur Administratoren ändern")
    with runtime.lock(sid):
        operations.require_stopped(sid)
        if store.query("SELECT id FROM servers WHERE port=? AND id!=?", (body.port, sid)):
            raise HTTPException(409, "UDP-Port bereits vergeben")
        store.execute("UPDATE servers SET name=?,port=?,memory_mb=?,cpus=?,autostart=?,autorestart=?,patchline=? WHERE id=?",
                      (body.name, body.port, body.memory_mb, body.cpus, body.autostart, body.autorestart, body.patchline, sid))
        audit(user, "server.edit", sid)
    return server_state(access(user, sid), user)


@app.delete("/api/servers/{sid}")
def delete_server(sid: str, user: User, confirmation: str):
    server = access(user, sid, manage=True)
    if confirmation != server["name"]:
        raise HTTPException(400, "Servername als Bestätigung erforderlich")
    with runtime.lock(sid):
        operations.require_stopped(sid)
        operations.backup(server, user, full=True)
        for install in (False, True):
            c = runtime.container(sid, install)
            if c:
                c.remove()
        shutil.rmtree(root(sid))
        store.execute("DELETE FROM servers WHERE id=?", (sid,))
        audit(user, "server.delete", sid, server["name"])
    return {"ok": True, "backup_directory": sid}


@app.post("/api/servers/{sid}/clone")
def clone_server(sid: str, body: ServerSpec, user: User):
    access(user, sid, manage=True)
    with runtime.lock(sid):
        operations.require_stopped(sid)
        new = insert_server(body)
        try:
            # Clone game/config data, but never share OAuth credentials or machine identity.
            for p in root(sid).rglob("*"):
                rel = p.relative_to(root(sid))
                if p.is_symlink() or any(part in PROTECTED or part.startswith(".hytale") or part == ".machine-id" for part in rel.parts):
                    continue
                dst = root(new["id"]) / rel
                if p.is_dir():
                    dst.mkdir(parents=True, exist_ok=True)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
            owned_tree(root(new["id"]))
        except Exception:
            store.execute("DELETE FROM servers WHERE id=?", (new["id"],))
            shutil.rmtree(root(new["id"]), ignore_errors=True)
            raise
        audit(user, "server.clone", new["id"], sid)
    return server_state(new, user)


@app.post("/api/servers/{sid}/power/{action}")
def power(sid: str, action: Literal["start", "stop", "restart", "kill"], user: User):
    if store.query("SELECT id FROM jobs WHERE server_id=? AND state IN ('queued','running')", (sid,)):
        raise HTTPException(409, 'Zuerst den laufenden Auftrag abschließen oder abbrechen')
    server = access(user, sid, right="server.power")
    with runtime.lock(sid):
        if action in {"stop", "restart", "kill"}:
            runtime.stop(sid, force=action == "kill")
        if action in {"start", "restart"}:
            runtime.launch(server)
        audit(user, "server." + action, sid)
    return server_state(server, user)


@app.post("/api/servers/{sid}/install")
def install(sid: str, user: User):
    server = access(user, sid, manage=True)
    with runtime.lock(sid):
        operations.require_stopped(sid)
        if (root(sid) / "Server/HytaleServer.jar").exists():
            operations.backup(server, user, full=True)
        runtime.launch(server, install=True)
        audit(user, "server.install", sid)
    return {"ok": True}


class Command(BaseModel):
    command: str = Field(min_length=1, max_length=2048)


@app.post("/api/servers/{sid}/command")
def command(sid: str, body: Command, user: User):
    access(user, sid, right="console.write")
    runtime.command(sid, body.command)
    # Command contents may contain passwords/tokens; only log the verb.
    audit(user, "console.command", sid, body.command.split()[0])
    return {"ok": True}


def clean_logs(value):
    value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)
    value = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[TOKEN REDACTED]", value)
    return value


@app.get("/api/servers/{sid}/logs")
def logs(sid: str, user: User, install: bool = False, download: bool = False):
    access(user, sid, right="console.read")
    if install:
        admin(user)
    content = clean_logs(runtime.logs(sid, install, tail="all" if download else 500))
    if download:
        return Response(content, media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="console.log"'})
    return {"logs": content}


@app.get("/api/servers/{sid}/events")
async def events(sid: str, request: Request, user: User):
    access(user, sid, right="console.read")
    async def stream():
        previous = None
        for _ in range(1800):
            if await request.is_disconnected():
                return
            try:
                current_user(request)
                access(user, sid, right="console.read")
                value = await asyncio.to_thread(runtime.logs, sid, False, 300)
                value = clean_logs(value)
                if value != previous:
                    yield "data: " + json.dumps({"logs": value}) + "\n\n"
                    previous = value
                else:
                    yield ": heartbeat\n\n"
            except Exception:
                yield 'event: closed\ndata: {}\n\n'
                return
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/api/servers/{sid}/files")
def files(sid: str, user: User, path: str = "Server"):
    access(user, sid, right="files.read")
    directory = safe_path(root(sid), path)
    if not directory.is_dir():
        raise HTTPException(404, "Verzeichnis nicht gefunden")
    result = []
    for p in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if p.is_symlink() or p.name in PROTECTED or p.name.startswith(".hytale") or p.name == ".machine-id":
            continue
        s = p.stat()
        result.append({"name": p.name, "path": p.relative_to(root(sid)).as_posix(), "directory": p.is_dir(),
                       "size": s.st_size if p.is_file() else 0, "modified": s.st_mtime})
    return result


@app.get("/api/servers/{sid}/files/content")
def file_content(sid: str, user: User, path: str):
    access(user, sid, right="files.read")
    p = safe_path(root(sid), path)
    if not p.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    if p.stat().st_size > 2 * 1024**2:
        raise HTTPException(400, "Texteditor ist auf 2 MiB begrenzt; Datei herunterladen")
    try:
        return {"content": p.read_text(encoding="utf-8"), "path": path}
    except UnicodeDecodeError:
        raise HTTPException(400, "Binärdatei kann nicht im Texteditor geöffnet werden")


@app.get("/api/servers/{sid}/files/download")
def file_download(sid: str, user: User, path: str):
    access(user, sid, right="files.read")
    p = safe_path(root(sid), path)
    if not p.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


class FileEdit(BaseModel):
    path: str
    content: str = Field(max_length=2 * 1024**2)


@app.put("/api/servers/{sid}/files/content")
def edit_file(sid: str, body: FileEdit, user: User):
    access(user, sid, right="files.write")
    with runtime.lock(sid):
        operations.require_stopped(sid)
        p = safe_path(root(sid), body.path)
        if p == root(sid):
            raise HTTPException(400, "Dateipfad erforderlich")
        if p.suffix == ".json":
            try:
                json.loads(body.content)
            except json.JSONDecodeError:
                raise HTTPException(400, "Ungültiges JSON")
        atomic_write(p, body.content.encode())
        audit(user, "file.write", sid, body.path)
    return {"ok": True}


def save_upload(upload, destination):
    temporary = destination.with_name(destination.name + ".upload-partial")
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("wb") as f:
            while chunk := upload.file.read(1024**2):
                total += len(chunk)
                if total > UPLOAD_LIMIT:
                    raise HTTPException(413, "Upload überschreitet das konfigurierte Limit")
                f.write(chunk)
        os.replace(temporary, destination)
        owned_tree(destination)
    finally:
        temporary.unlink(missing_ok=True)
        upload.file.close()


@app.post("/api/servers/{sid}/files/upload")
def upload_file(sid: str, user: User, file: UploadFile = File(), path: str = Form("Server/mods"), overwrite: bool = Form(False)):
    access(user, sid, right="files.write")
    with runtime.lock(sid):
        operations.require_stopped(sid)
        if not file.filename or "/" in file.filename or "\\" in file.filename:
            raise HTTPException(400, "Ungültiger Dateiname")
        p = safe_path(root(sid), path + "/" + file.filename)
        if p.exists() and not overwrite:
            raise HTTPException(409, "Datei existiert bereits")
        save_upload(file, p)
        audit(user, "file.upload", sid, p.relative_to(root(sid)).as_posix())
    return {"ok": True}


class PathAction(BaseModel):
    path: str
    destination: str = ""


@app.post("/api/servers/{sid}/files/{action}")
def path_action(sid: str, action: Literal["mkdir", "rename", "delete"], body: PathAction, user: User):
    access(user, sid, right="files.write")
    with runtime.lock(sid):
        operations.require_stopped(sid)
        p = safe_path(root(sid), body.path)
        if p == root(sid).resolve() or p == (root(sid) / "Server").resolve():
            raise HTTPException(400, "Serverwurzel ist geschützt")
        if action == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            owned_tree(p)
        elif action == "rename":
            target = safe_path(root(sid), body.destination)
            if target.exists():
                raise HTTPException(409, "Ziel existiert bereits")
            if not p.exists():
                raise HTTPException(404, "Datei nicht gefunden")
            target.parent.mkdir(parents=True, exist_ok=True)
            p.rename(target)
            owned_tree(target.parent)
        else:
            if not p.exists():
                raise HTTPException(404, "Datei nicht gefunden")
            if p.is_dir():
                # Protected credentials must not be removed through a parent directory.
                if any(q.name in PROTECTED for q in p.rglob("*")):
                    raise HTTPException(403, "Verzeichnis enthält geschützte Anmeldedaten")
                shutil.rmtree(p)
            else:
                p.unlink()
        audit(user, "file." + action, sid, body.path)
    return {"ok": True}


@app.post("/api/servers/{sid}/import")
def import_server(sid: str, user: User, file: UploadFile = File()):
    access(user, sid, manage=True)
    with runtime.lock(sid):
        operations.require_stopped(sid)
        staging = store.DATA / "uploads" / uuid.uuid4().hex
        staging.mkdir()
        try:
            archive = staging / "package.zip"
            save_upload(file, archive)
            unpacked = staging / "unpacked"
            unpacked.mkdir()
            extract_zip(archive, unpacked)
            jars, assets = list(unpacked.rglob("HytaleServer.jar")), list(unpacked.rglob("Assets.zip"))
            if len(jars) != 1 or len(assets) != 1:
                raise HTTPException(400, "ZIP muss HytaleServer.jar und Assets.zip enthalten")
            if (root(sid) / "Server/HytaleServer.jar").exists():
                operations.backup(access(user, sid), user, full=True)
            for name in ("HytaleServer.jar", "HytaleServer.aot"):
                src = jars[0].parent / name
                if src.is_file():
                    shutil.copy2(src, root(sid) / "Server" / name)
            shutil.copy2(assets[0], root(sid) / "Assets.zip")
            for name in ("start.sh", "start.bat"):
                paths = list(unpacked.rglob(name))
                if paths:
                    shutil.copy2(paths[0], root(sid) / name)
            owned_tree(root(sid))
            audit(user, "server.import", sid)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True}


@app.get("/api/servers/{sid}/mods")
def mods(sid: str, user: User):
    return [dict(f, enabled=not f["name"].endswith(".disabled")) for f in files(sid, user, "Server/mods") if not f["directory"]]


@app.post("/api/servers/{sid}/mods/toggle")
def toggle_mod(sid: str, body: PathAction, user: User):
    if "/" in body.path or "\\" in body.path or not body.path.endswith((".jar", ".zip", ".jar.disabled", ".zip.disabled")):
        raise HTTPException(400, "Mod muss eine JAR- oder ZIP-Datei sein")
    destination = body.path.removesuffix(".disabled") if body.path.endswith(".disabled") else body.path + ".disabled"
    return path_action(sid, "rename", PathAction(path="Server/mods/" + body.path, destination="Server/mods/" + destination), user)


@app.get("/api/servers/{sid}/worlds")
def worlds(sid: str, user: User):
    access(user, sid)
    result = []
    directory = root(sid) / "Server/universe/worlds"
    if directory.exists():
        for p in directory.iterdir():
            if p.is_dir() and not p.is_symlink():
                result.append({"name": p.name, "config_path": "Server/universe/worlds/" + p.name + "/config.json"})
    return result


@app.get("/api/servers/{sid}/backups")
def backups(sid: str, user: User):
    access(user, sid)
    directory = store.DATA / "backups" / sid
    return sorted([{"name": p.name, "size": p.stat().st_size, "created": p.stat().st_mtime}
                   for p in directory.glob("*.zip")], key=lambda p: p["created"], reverse=True)


@app.post("/api/servers/{sid}/backups")
def create_backup(sid: str, user: User, full: bool = False):
    return operations.backup(access(user, sid, right="backups.create"), user, full=full)


@app.get("/api/servers/{sid}/backups/{name}")
def download_backup(sid: str, name: str, user: User):
    access(user, sid)
    p = safe_path(store.DATA / "backups" / sid, name)
    if not p.is_file() or p.suffix != ".zip":
        raise HTTPException(404, "Backup nicht gefunden")
    return FileResponse(p, filename=p.name, media_type="application/zip")


@app.post("/api/servers/{sid}/backups/{name}/restore")
def restore_backup(sid: str, name: str, user: User):
    operations.restore(access(user, sid, right="backups.restore"), name, user)
    return {"ok": True}


@app.delete("/api/servers/{sid}/backups/{name}")
def delete_backup(sid: str, name: str, user: User):
    access(user, sid, manage=True)
    p = safe_path(store.DATA / "backups" / sid, name)
    if p.suffix != ".zip" or not p.is_file():
        raise HTTPException(404, "Backup nicht gefunden")
    p.unlink()
    audit(user, "backup.delete", sid, name)
    return {"ok": True}


class Schedule(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    cron: str
    timezone: str = "Europe/Berlin"
    action: Literal["start", "stop", "restart", "backup", "command", "update"]
    command: str = Field(default="", max_length=2048)
    enabled: bool = True


@app.get("/api/servers/{sid}/schedules")
def schedules(sid: str, user: User):
    access(user, sid)
    return store.query("SELECT * FROM schedules WHERE server_id=?", (sid,))


@app.post("/api/servers/{sid}/schedules", status_code=201)
def create_schedule(sid: str, body: Schedule, user: User):
    access(user, sid, right="automation.write")
    if body.action == "command" and (not body.command or any(c in body.command for c in "\r\n\x00")):
        raise HTTPException(400, "Einzelnen Konsolenbefehl angeben")
    next_at = operations.next_run(body.cron, body.timezone)
    jid = uuid.uuid4().hex
    store.execute("INSERT INTO schedules(id,server_id,name,cron,timezone,action,command,enabled,next_run) VALUES(?,?,?,?,?,?,?,?,?)",
                  (jid, sid, body.name, body.cron, body.timezone, body.action, body.command, body.enabled, next_at))
    audit(user, "schedule.create", sid, body.name)
    return store.query("SELECT * FROM schedules WHERE id=?", (jid,), one=True)


@app.delete("/api/servers/{sid}/schedules/{jid}")
def delete_schedule(sid: str, jid: str, user: User):
    access(user, sid, right="automation.write")
    store.execute("DELETE FROM schedules WHERE id=? AND server_id=?", (jid, sid))
    audit(user, "schedule.delete", sid, jid)
    return {"ok": True}


class UserSpec(Credentials):
    role: Literal["admin", "member"] = "member"


@app.get("/api/users")
def users(user: User):
    admin(user)
    return store.query("SELECT id,username,role,disabled FROM users ORDER BY username")


@app.post("/api/users", status_code=201)
def create_user(body: UserSpec, user: User):
    admin(user)
    if store.query("SELECT id FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "Benutzername bereits vergeben")
    uid = uuid.uuid4().hex
    store.execute("INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)",
                  (uid, body.username, hash_password(body.password), body.role))
    audit(user, "user.create", detail=body.username)
    return {"id": uid}


class UserUpdate(BaseModel):
    role: Literal["admin", "member"]
    disabled: bool = False
    password: str = ""


@app.put("/api/users/{uid}")
def update_user(uid: str, body: UserUpdate, user: User):
    admin(user)
    existing = store.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not existing:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if existing["role"] == "admin" and (body.role != "admin" or body.disabled):
        if len(store.query("SELECT id FROM users WHERE role='admin' AND disabled=0 AND id!=?", (uid,))) == 0:
            raise HTTPException(409, "Der letzte aktive Administrator darf nicht deaktiviert werden")
    encoded = hash_password(body.password) if body.password else existing["password"]
    store.execute("UPDATE users SET role=?,disabled=?,password=? WHERE id=?", (body.role, body.disabled, encoded, uid))
    store.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    store.execute("DELETE FROM tokens WHERE user_id=?", (uid,))
    audit(user, "user.edit", detail=existing["username"])
    return {"ok": True}


class Permission(BaseModel):
    user_id: str
    permission: Literal["viewer", "operator", "none", "custom"]
    rights: list[str] | None = None


@app.get("/api/servers/{sid}/access")
def server_access(sid: str, user: User):
    access(user, sid, right="access.write")
    rows = store.query("SELECT a.*,u.username FROM access a JOIN users u ON a.user_id=u.id WHERE server_id=?", (sid,))
    return [dict(row, rights=rights({'id':row['user_id'], 'role':'member'}, sid)) for row in rows]


@app.put("/api/servers/{sid}/access")
def assign_access(sid: str, body: Permission, user: User):
    access(user, sid, right="access.write")
    granted = body.rights if body.rights is not None else RIGHTS if body.permission == "operator" else ["console.read", "files.read"]
    if user["role"] != "admin" and (body.user_id == user["id"] or not set(granted).issubset(set(rights(user, sid)))):
        raise HTTPException(403, "Nur eigene Rechte an andere Mitglieder weitergeben")
    if not store.query("SELECT id FROM users WHERE id=?", (body.user_id,)):
        raise HTTPException(404, "Benutzer nicht gefunden")
    if body.permission == "none":
        store.execute("DELETE FROM access WHERE server_id=? AND user_id=?", (sid, body.user_id))
        store.execute("DELETE FROM settings WHERE key=?", ('rights:' + sid + ':' + body.user_id,))
    else:
        if body.rights is not None and any(value not in RIGHTS for value in body.rights):
            raise HTTPException(400, 'Unbekannte Serverberechtigung')
        store.execute("INSERT OR REPLACE INTO access VALUES(?,?,?)", (sid, body.user_id, body.permission))
        store.set_setting('rights:' + sid + ':' + body.user_id, body.rights if body.rights is not None else RIGHTS if body.permission == 'operator' else ['console.read', 'files.read'])
    audit(user, "access.edit", sid, body.user_id + ":" + body.permission)
    return {"ok": True}


@app.get("/api/audit")
def audit_log(user: User, q: str = "", server_id: str = "", action: str = ""):
    admin(user)
    return store.query("SELECT * FROM audit WHERE (?='' OR server_id=?) AND (?='' OR action=?) "
                       "AND (?='' OR user LIKE ? OR detail LIKE ? OR action LIKE ?) ORDER BY id DESC LIMIT 500",
                       (server_id, server_id, action, action, q, '%' + q[:100] + '%', '%' + q[:100] + '%', '%' + q[:100] + '%'))


@app.get("/api/system")
def system(user: User):
    admin(user)
    try:
        docker_info = runtime.client().info()
        docker_state = {"connected": True, "version": docker_info.get("ServerVersion"), "cpus": docker_info.get("NCPU"),
                        "memory_bytes": docker_info.get("MemTotal")}
    except Exception:
        docker_state = {"connected": False}
    disk = shutil.disk_usage(store.DATA)
    return {"version": VERSION, "docker": docker_state, "runner_image": runtime.IMAGE, "host_data": runtime.HOST_DATA,
            "disk": {"total": disk.total, "free": disk.free}, "backup_retention": store.setting("backup_retention", 10), "backup_global_quota_gb": store.setting("backup_global_quota_gb", 100),
            "public_url": os.getenv("HYPANEL_PUBLIC_URL", ""), "upload_limit_bytes": UPLOAD_LIMIT}


class Settings(BaseModel):
    backup_global_quota_gb: float = Field(default=100, ge=1, le=100000)
    backup_retention: int = Field(ge=2, le=365, default=10)


@app.put("/api/system")
def update_settings(body: Settings, user: User):
    admin(user)
    store.set_setting("backup_retention", body.backup_retention)
    store.set_setting("backup_global_quota_gb", body.backup_global_quota_gb)
    audit(user, "settings.edit")
    return {"ok": True}


class TokenSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    days: int = Field(default=90, ge=1, le=365)


@app.get("/api/me/tokens")
def tokens(user: User):
    return store.query("SELECT id,name,created,expires FROM tokens WHERE user_id=? ORDER BY created DESC", (user["id"],))


@app.post("/api/me/tokens", status_code=201)
def create_token(body: TokenSpec, user: User):
    if not user["csrf"]:
        raise HTTPException(403, "Neue API-Schlüssel nur nach Browseranmeldung erstellen")
    value = "hyp_" + secrets.token_urlsafe(48)
    tid = uuid.uuid4().hex
    store.execute("INSERT INTO tokens VALUES(?,?,?,?,?,?)", (tid, digest(value), user["id"], body.name,
                  time.time(), time.time() + body.days * 86400))
    audit(user, "token.create", detail=body.name)
    return {"id": tid, "token": value}


@app.delete("/api/me/tokens/{tid}")
def delete_token(tid: str, user: User):
    store.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (tid, user["id"]))
    audit(user, "token.revoke", detail=tid)
    return {"ok": True}


@app.put("/api/servers/{sid}/schedules/{jid}")
def edit_schedule(sid: str, jid: str, body: Schedule, user: User):
    access(user, sid, right="automation.write")
    if not store.query("SELECT id FROM schedules WHERE id=? AND server_id=?", (jid, sid)):
        raise HTTPException(404, "Zeitplan nicht gefunden")
    if body.action == "command" and (not body.command or any(c in body.command for c in "\r\n\x00")):
        raise HTTPException(400, "Einzelnen Konsolenbefehl angeben")
    store.execute("UPDATE schedules SET name=?,cron=?,timezone=?,action=?,command=?,enabled=?,next_run=? WHERE id=? AND server_id=?",
                  (body.name, body.cron, body.timezone, body.action, body.command, body.enabled,
                   operations.next_run(body.cron, body.timezone), jid, sid))
    audit(user, "schedule.edit", sid, body.name)
    return {"ok": True}


@app.post("/api/servers/{sid}/schedules/{jid}/run")
def run_schedule(sid: str, jid: str, user: User):
    access(user, sid, right="automation.write")
    job = store.query("SELECT * FROM schedules WHERE id=? AND server_id=?", (jid, sid), one=True)
    if not job:
        raise HTTPException(404, "Zeitplan nicht gefunden")
    operations.scheduled_action(job)
    store.execute("UPDATE schedules SET last_run=?,last_result='OK' WHERE id=?", (time.time(), jid))
    audit(user, "schedule.run", sid, jid)
    return {"ok": True}


app.include_router(features_router)
FRONTEND = Path(os.getenv("HYPANEL_FRONTEND", str(Path(__file__).resolve().parents[2] / "frontend")))
if FRONTEND.exists():
    @app.get("/docs", include_in_schema=False)
    def api_docs():
        return FileResponse(FRONTEND / "api.html")
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
