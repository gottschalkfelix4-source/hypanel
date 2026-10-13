"""Hytale management screens adapted from MCPanel's workflows."""
import json
import re
import time
import os
import shutil
import uuid
import zipfile
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import store, runtime, metrics, jobs, operations, providers
from .files import root, safe_path, atomic_write, extract_zip
from .security import current_user, access, admin, audit

router = APIRouter(prefix="/api")
User = Annotated[dict, Depends(current_user)]


@router.get('/servers/{sid}/access/candidates')
def candidates(sid: str, user: User):
    access(user, sid, right='access.write')
    return store.query("SELECT id,username,role FROM users WHERE role='member' AND disabled=0 ORDER BY username")


@router.get("/servers/{sid}/metrics/history")
def history(sid: str, user: User, hours: int = 1):
    access(user, sid)
    return metrics.history(sid, min(72, max(1, hours)))


@router.get("/servers/{sid}/overview")
def overview(sid: str, user: User):
    server = access(user, sid)
    state = runtime.state(server, metrics=True)
    telemetry = metrics.telemetry(sid, state["status"] == "running")
    return {"telemetry": telemetry, "backup_usage": operations.backup_usage(sid),
            "backup_policy": operations.backup_policy(sid),
            "jobs": store.query("SELECT * FROM jobs WHERE server_id=? ORDER BY created DESC LIMIT 10", (sid,)),
            "activity": store.query("SELECT * FROM audit WHERE server_id=? ORDER BY id DESC LIMIT 10", (sid,)),
            "managed_mods": store.query("SELECT * FROM managed_mods WHERE server_id=?", (sid,)),
            "ready": telemetry["available"] if state["status"] == "running" else False}


@router.get("/servers/{sid}/players")
def players(sid: str, user: User):
    server = access(user, sid)
    data = metrics.telemetry(sid, runtime.state(server)["status"] == "running")
    return {"online": data["players"], "available": data["available"],
            "known": store.query("SELECT * FROM known_players WHERE server_id=? ORDER BY last_seen DESC", (sid,))}


class PlayerAction(BaseModel):
    action: Literal['kick', 'ban', 'unban', 'whitelist add', 'whitelist remove', 'op add', 'op remove']
    player: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


@router.post('/servers/{sid}/players/action')
def player_action(sid: str, body: PlayerAction, user: User):
    access(user, sid, right='players.write')
    runtime.command(sid, body.action + ' ' + body.player)
    audit(user, 'player.' + body.action.split()[0], sid, body.player)
    return {'ok': True}


@router.get("/host")
def host(user: User):
    admin(user)
    return metrics.host()


@router.get("/jobs")
def all_jobs(user: User, server_id: str | None = None):
    if server_id:
        access(user, server_id)
        return store.query("SELECT * FROM jobs WHERE server_id=? ORDER BY created DESC LIMIT 100", (server_id,))
    admin(user)
    return store.query("SELECT j.*,s.name AS server_name FROM jobs j LEFT JOIN servers s ON s.id=j.server_id ORDER BY created DESC LIMIT 200")


@router.get("/jobs/{jid}")
def job(jid: str, user: User):
    row = store.query("SELECT * FROM jobs WHERE id=?", (jid,), one=True)
    if not row:
        raise HTTPException(404, "Auftrag nicht gefunden")
    access(user, row["server_id"])
    return row


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str, user: User):
    row = job(jid, user)
    access(user, row["server_id"], right="server.power")
    if row["state"] not in {"queued", "running"} or not row["cancellable"]:
        raise HTTPException(409, "Dieser Auftrag lässt sich nicht mehr abbrechen")
    store.execute("UPDATE jobs SET cancel=1 WHERE id=?", (jid,))
    audit(user, "job.cancel", row["server_id"], jid)
    return {"ok": True}


class Task(BaseModel):
    action: Literal["backup", "restore", "install", "mod"]
    full: bool = False
    name: str = ""
    project_id: int = Field(default=0, ge=0)
    file_id: int = Field(default=0, ge=0)


@router.post("/servers/{sid}/tasks", status_code=202)
def task(sid: str, body: Task, user: User):
    server = access(user, sid, manage=body.action == "install", right={"backup":"backups.create", "restore":"backups.restore", "mod":"mods.write"}.get(body.action))
    if runtime.EXTERNAL_WRITES.get(sid, 0):
        raise HTTPException(409, 'Zuerst laufende SFTP-Uploads abschließen')
    if body.action == "backup":
        operation = lambda ctx: operations.backup(server, user, full=body.full, ctx=ctx)
    elif body.action == "restore":
        operations.require_stopped(sid)
        def operation(ctx):
            ctx.update(5, "Archiv prüfen und Sicherheitsbackup erstellen", cancellable=False)
            operations.restore(server, body.name, user)
            return {"name": body.name}
    elif body.action == "install":
        operations.require_stopped(sid)
        def operation(ctx):
            if runtime.state(server)["installed"]:
                operations.backup(server, user, full=True, ctx=ctx)
            ctx.update(10, "Offizieller Downloader: Geräteanmeldung im Installationsprotokoll bestätigen")
            runtime.launch(server, install=True)
            try:
                while True:
                    ctx.check()
                    c = runtime.container(sid, True)
                    if not c:
                        raise HTTPException(409, "Downloader-Container fehlt")
                    if c.status not in {"running", "restarting"}:
                        if c.attrs.get("State", {}).get("ExitCode", 1) != 0:
                            raise HTTPException(409, "Downloader fehlgeschlagen. Installationsprotokoll prüfen.")
                        break
                    time.sleep(2)
                ctx.update(99, "Serverdateien installiert", cancellable=False)
                return {"installed": runtime.state(server)["installed"]}
            finally:
                c = runtime.container(sid, True)
                if c and c.status == "running":
                    c.stop(timeout=10)
    else:
        operations.require_stopped(sid)
        operation = lambda ctx: providers.install(server, body.project_id, body.file_id, user, ctx)
    return jobs.submit(sid, body.action, user, operation)


class BackupPolicy(BaseModel):
    retention: int = Field(default=10, ge=2, le=365)
    quota_gb: float = Field(default=20, ge=1, le=100000)


@router.get("/servers/{sid}/backup-policy")
def backup_policy(sid: str, user: User):
    access(user, sid)
    return dict(operations.backup_policy(sid), used_bytes=operations.backup_usage(sid))


@router.put("/servers/{sid}/backup-policy")
def save_policy(sid: str, body: BackupPolicy, user: User):
    access(user, sid, manage=True)
    store.set_setting("backup_retention:" + sid, body.retention)
    store.set_setting("backup_quota_gb:" + sid, body.quota_gb)
    audit(user, "backup.policy", sid)
    return {"ok": True}


@router.get("/providers/curseforge")
def provider_state(user: User):
    admin(user)
    return {"configured": bool(providers.key())}


class ProviderKey(BaseModel):
    key: str = Field(min_length=10, max_length=512)


@router.put("/providers/curseforge")
def save_key(body: ProviderKey, user: User):
    admin(user)
    # Test with the proposed credential before committing settings.
    import urllib.request
    try:
        req = urllib.request.Request(providers.BASE + "/games?pageSize=1", headers={"x-api-key": body.key})
        with providers.api_open(req, timeout=20) as response:
            json.loads(response.read(1024 * 1024))
    except Exception:
        raise HTTPException(400, "CurseForge hat den API-Schlüssel nicht akzeptiert oder ist nicht erreichbar")
    store.set_setting("curseforge_key", body.key)
    audit(user, "provider.key.save")
    return {"configured": True}


@router.delete("/providers/curseforge")
def remove_key(user: User):
    admin(user)
    store.execute("DELETE FROM settings WHERE key IN ('curseforge_key','curseforge_hytale_game')")
    audit(user, "provider.key.delete")
    return {"configured": bool(providers.key())}


@router.get("/servers/{sid}/catalog")
def catalog(sid: str, user: User, q: str = "", index: int = 0):
    access(user, sid)
    return providers.search(q[:100], min(10000, max(0, index)))


@router.get("/servers/{sid}/catalog/{pid}/versions")
def versions(sid: str, pid: int, user: User):
    access(user, sid)
    return providers.versions(pid)


@router.get("/servers/{sid}/mod-updates")
def mod_updates(sid: str, user: User):
    access(user, sid)
    return store.query("SELECT * FROM managed_mods WHERE server_id=?", (sid,))


@router.post("/servers/{sid}/mod-updates/check")
def check_updates(sid: str, user: User):
    access(user, sid, right="mods.write")
    return providers.check_updates(sid)


CONFIG_FIELDS = {
    "ServerName": (str, "Servername", None, None), "MOTD": (str, "MOTD", None, None),
    "MaxPlayers": (int, "Maximale Spielerzahl", 1, 10000),
    "MaxViewRadius": (int, "Maximale Sichtweite", 1, 64),
}


@router.get("/servers/{sid}/config-form")
def config_form(sid: str, user: User):
    access(user, sid)
    path = safe_path(root(sid), "Server/config.json")
    try:
        value = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(value, dict):
            raise ValueError()
    except Exception:
        raise HTTPException(400, "Serverkonfiguration enthält kein gültiges JSON")
    return {"fields": [{"key": key, "label": label, "type": "number" if kind is int else "text", "min": low, "max": high,
                        "value": value.get(key, ""), "present": key in value}
                       for key, (kind, label, low, high) in CONFIG_FIELDS.items()]}


class ConfigEdit(BaseModel):
    values: dict


@router.put("/servers/{sid}/config-form")
def save_config(sid: str, body: ConfigEdit, user: User):
    access(user, sid, right="config.write")
    with runtime.lock(sid):
        operations.require_stopped(sid)
        path = safe_path(root(sid), "Server/config.json")
        try:
            current = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(current, dict):
                raise ValueError()
        except Exception:
            raise HTTPException(400, "Konfiguration enthält kein gültiges JSON")
        for key, value in body.values.items():
            if key not in CONFIG_FIELDS:
                raise HTTPException(400, "Unbekannte Einstellung")
            kind, _, low, high = CONFIG_FIELDS[key]
            if type(value) is not kind or (kind is int and not low <= value <= high) or (kind is str and len(value) > 512):
                raise HTTPException(400, "Ungültiger Wert für " + key)
            current[key] = value
        atomic_write(path, (json.dumps(current, indent=2, ensure_ascii=False) + "\n").encode())
    audit(user, "config.edit", sid)
    return {"ok": True}


@router.get("/servers/{sid}/diagnosis")
def diagnosis(sid: str, user: User):
    access(user, sid)
    text = runtime.logs(sid, tail=300)
    findings = []
    rules = [(r"OutOfMemoryError|Cannot allocate memory", "Arbeitsspeicher reicht nicht aus", "RAM-Limit prüfen und ressourcenintensive Mods reduzieren."),
             (r"UnsupportedClassVersionError", "Java-Version passt nicht", "Java 25 und aktuelles Runner-Image verwenden."),
             (r"Address already in use|BindException", "UDP-Port ist belegt", "Einen freien Serverport in den Einstellungen wählen."),
             (r"Failed to load plugin|Could not load plugin|Missing dependency", "Plugin konnte nicht geladen werden", "Plugin-Version und erforderliche Abhängigkeiten prüfen."),
             (r"No server tokens|No valid authentication|not authenticated", "Hytale-Anmeldung fehlt", "In der Konsole auth login device ausführen und anschließend die Anmeldung verschlüsselt speichern.")]
    for expression, title, hint in rules:
        if re.search(expression, text, re.I):
            findings.append({"title": title, "hint": hint})
    return {"findings": findings}


@router.get('/servers/{sid}/sftp')
def sftp_details(sid: str, user: User):
    access(user, sid, right='files.read')
    return {'enabled':os.getenv('HYPANEL_SFTP_ENABLED','true').lower()=='true',
            'username':user['username'] + '+' + sid, 'port':int(os.getenv('HYPANEL_SFTP_PUBLIC_PORT','2223'))}


class Extract(BaseModel):
    path: str
    destination: str


@router.post('/servers/{sid}/archive/extract')
def unpack(sid: str, body: Extract, user: User):
    access(user, sid, right='files.write')
    with runtime.lock(sid):
        operations.require_stopped(sid)
        if runtime.EXTERNAL_WRITES.get(sid, 0):
            raise HTTPException(409, 'Zuerst laufende SFTP-Uploads abschließen')
        archive = safe_path(root(sid), body.path)
        target = safe_path(root(sid), body.destination)
        if target.exists() or not archive.is_file() or archive.suffix.lower() != '.zip':
            raise HTTPException(400, 'ZIP-Datei und neues Zielverzeichnis angeben')
        stage = store.DATA / 'uploads' / uuid.uuid4().hex
        try:
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    safe_path(target, info.orig_filename)
                    safe_path(target, info.filename)
            stage.mkdir()
            extract_zip(archive, stage)
            from .files import owned_tree
            owned_tree(stage)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage), target)
        except zipfile.BadZipFile:
            raise HTTPException(400, 'Ungültiges ZIP-Archiv')
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    audit(user, 'file.extract', sid, body.path)
    return {'ok':True}


@router.get('/panel-backups')
def panel_backups(user: User):
    admin(user)
    return [{'name':p.name,'size':p.stat().st_size,'created':p.stat().st_mtime}
            for p in sorted((store.DATA / 'panel-backups').glob('*.zip'), reverse=True) if not p.is_symlink()]


@router.post('/panel-backups')
def create_panel_backup(user: User):
    admin(user)
    directory=store.DATA / 'panel-backups';directory.mkdir(exist_ok=True)
    name=time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.zip'
    temporary=directory/(name + '.partial')
    snapshot=directory/(uuid.uuid4().hex + '.db')
    import sqlite3
    try:
        from contextlib import closing
        with store.db() as source, closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination)
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot,'panel.db')
            for filename in ('sftp-host.key','setup.key'):
                path=store.DATA/filename
                if path.is_file() and not path.is_symlink():z.write(path,filename)
            for path in (store.DATA/'identities').glob('*'):
                if path.is_file() and not path.is_symlink():z.write(path,'identities/'+path.name)
            z.writestr('WIEDERHERSTELLUNG.txt', 'HyPanel stoppen. Archiv in ein neues Appdata-Verzeichnis entpacken. Serverdateien und Server-Backups separat aus ihrer Sicherung übernehmen. Appdata und HYPANEL_HOST_DATA auf dieses Verzeichnis setzen. HyPanel mit derselben oder neuerer Version starten. Enthält Zugangsdaten: Archiv vertraulich behandeln.\n')
        os.chmod(temporary,0o600);os.replace(temporary,directory/name)
        audit(user,'panel.backup')
        return {'name':name}
    finally:
        temporary.unlink(missing_ok=True);snapshot.unlink(missing_ok=True)


@router.get('/panel-backups/{name}')
def download_panel_backup(name: str, user: User):
    admin(user)
    from fastapi.responses import FileResponse
    path=safe_path(store.DATA/'panel-backups',name)
    if path.suffix!='.zip' or not path.is_file():raise HTTPException(404,'Panel-Sicherung nicht gefunden')
    return FileResponse(path,filename=name,media_type='application/zip')


@router.delete('/panel-backups/{name}')
def delete_panel_backup(name: str, user: User):
    admin(user)
    path=safe_path(store.DATA/'panel-backups',name)
    if path.suffix!='.zip' or not path.is_file():raise HTTPException(404,'Panel-Sicherung nicht gefunden')
    path.unlink();audit(user,'panel.backup.delete')
    return {'ok':True}
