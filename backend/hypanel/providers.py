"""CurseForge Hytale catalog. Game identification comes from the provider API."""
import hashlib
import json
import os
import shutil
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid
import zipfile
from fastapi import HTTPException
from . import store, operations
from .files import root, safe_path, atomic_write

BASE = "https://api.curseforge.com/v1"


class ApiRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'api.curseforge.com' or parsed.port not in (None, 443) or parsed.username:
            raise HTTPException(502, 'Provider-Weiterleitung abgelehnt')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def api_open(req, timeout=30):
    return urllib.request.build_opener(ApiRedirect()).open(req, timeout=timeout)


def key():
    return store.setting("curseforge_key", None) or os.getenv("CURSEFORGE_API_KEY", "")


def request(path, params=None):
    if not key():
        raise HTTPException(503, "CurseForge-API-Schlüssel in den Panel-Einstellungen hinterlegen")
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        req = urllib.request.Request(url, headers={"x-api-key": key(), "Accept": "application/json", "User-Agent": "HyPanel/0.2"})
        with api_open(req) as response:
            data = response.read(8 * 1024**2 + 1)
        if len(data) > 8 * 1024**2:
            raise ValueError()
        return json.loads(data)
    except Exception:
        raise HTTPException(502, "CurseForge-Anfrage fehlgeschlagen. API-Schlüssel und Verbindung prüfen.")


def game():
    cached = store.setting("curseforge_hytale_game")
    if cached:
        return cached
    for offset in range(0, 1000, 50):
        data = request("/games", {"pageSize": 50, "index": offset})["data"]
        for item in data:
            if item.get("slug", "").lower() == "hytale" or item.get("name", "").lower() == "hytale":
                store.set_setting("curseforge_hytale_game", item["id"])
                return item["id"]
        if len(data) < 50:
            break
    raise HTTPException(503, "Hytale ist für diesen CurseForge-API-Schlüssel nicht verfügbar")


def project(pid):
    data = request(f"/mods/{pid}")["data"]
    if data.get("gameId") != game():
        raise HTTPException(400, "Dieses Projekt gehört nicht zu Hytale")
    return data


def search(query, index=0):
    data = request("/mods/search", {"gameId": game(), "searchFilter": query, "pageSize": 20,
                                   "index": index, "sortField": 2, "sortOrder": "desc"})
    return {"items": [{"id": p["id"], "name": p["name"], "summary": p.get("summary", ""),
                       "downloads": p.get("downloadCount", 0), "url": p.get("links", {}).get("websiteUrl", "")}
                      for p in data["data"]], "pagination": data.get("pagination", {})}


def versions(pid):
    project(pid)
    return [{"id": f["id"], "name": f["displayName"], "date": f.get("fileDate"),
             "versions": f.get("gameVersions", []), "size": f.get("fileLength"), "release_type": f.get("releaseType")}
            for f in request(f"/mods/{pid}/files", {"pageSize": 50})["data"] if f.get("isAvailable", True)]


def allowed_download(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or not any(
            host == suffix or host.endswith("." + suffix) for suffix in ("forgecdn.net", "cursecdn.com")):
        raise HTTPException(400, "Download-Adresse des Providers ist nicht zulässig")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_download(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(pid, fid, path, ctx):
    metadata = project(pid)
    info = request(f"/mods/{pid}/files/{fid}")["data"]
    if info.get("modId") != pid or not info.get("isAvailable", True):
        raise HTTPException(400, "Datei gehört nicht zum Projekt oder ist nicht verfügbar")
    url = info.get("downloadUrl") or request(f"/mods/{pid}/files/{fid}/download-url")["data"]
    allowed_download(url)
    expected = info.get("fileLength", 0)
    if expected <= 0 or expected > 2 * 1024**3:
        raise HTTPException(400, "Mod-Datei überschreitet das Limit von 2 GiB")
    sha1, md5, done, last = hashlib.sha1(), hashlib.md5(), 0, 0
    try:
        with urllib.request.build_opener(SafeRedirect()).open(url, timeout=60) as response, path.open("wb") as dst:
            while chunk := response.read(1024 * 1024):
                ctx.check()
                done += len(chunk)
                if done > expected:
                    raise HTTPException(400, "Download ist größer als angekündigt")
                dst.write(chunk); sha1.update(chunk); md5.update(chunk)
                if time.monotonic() - last > .5:
                    ctx.update(min(60, 5 + done / expected * 55), "Lade " + metadata["name"][:120])
                    last = time.monotonic()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Mod-Download fehlgeschlagen")
    if done != expected:
        raise HTTPException(400, "Download unvollständig")
    verified = False
    for checksum in info.get("hashes", []):
        actual = sha1.hexdigest() if checksum["algo"] == 1 else md5.hexdigest() if checksum["algo"] == 2 else None
        if actual:
            verified = True
            if actual.lower() != checksum["value"].lower():
                raise HTTPException(400, "Prüfsumme des Downloads stimmt nicht")
    if not verified:
        raise HTTPException(400, "Provider liefert keine unterstützte Prüfsumme")
    filename = info["fileName"]
    if "/" in filename or "\\" in filename or not filename.lower().endswith((".jar", ".zip")):
        raise HTTPException(400, "Dateiformat eignet sich nicht als Hytale-Mod")
    # Hytale plugins/asset packs contain their own manifest. Minecraft bundles are rejected.
    try:
        with zipfile.ZipFile(path) as archive:
            if "modrinth.index.json" in archive.namelist():
                raise ValueError()
            if archive.getinfo('manifest.json').file_size > 256 * 1024:
                raise ValueError()
            manifest = json.loads(archive.read("manifest.json"))
            if "minecraft" in manifest or not manifest.get("Group") or not manifest.get("Name"):
                raise ValueError()
    except Exception:
        raise HTTPException(400, "Kein Hytale-Plugin oder Asset-Pack mit gültiger manifest.json. Minecraft-Modpacks sind nicht kompatibel.")
    return metadata, info


def install(server, pid, fid, user, ctx):
    sid = server["id"]
    operations.require_stopped(sid)
    temp = store.DATA / "uploads" / (uuid.uuid4().hex + ".mod")
    try:
        metadata, info = download(pid, fid, temp, ctx)
        destination = safe_path(root(sid), "Server/mods/" + info["fileName"])
        old = store.query("SELECT * FROM managed_mods WHERE server_id=? AND project_id=?", (sid, pid), one=True)
        if destination.exists() and (not old or old["filename"] != info["fileName"]):
            raise HTTPException(409, "Dateiname bereits durch eine andere Mod belegt")
        if any(d.get("relationType") == 3 for d in info.get("dependencies", [])):
            required = [d["modId"] for d in info["dependencies"] if d.get("relationType") == 3]
            installed = {r["project_id"] for r in store.query("SELECT project_id,filename FROM managed_mods WHERE server_id=?", (sid,))
                         if safe_path(root(sid), 'Server/mods/' + r['filename']).is_file()}
            if set(required) - installed:
                raise HTTPException(409, "Zuerst die erforderlichen Abhängigkeiten installieren (Projekt-IDs: " + ", ".join(map(str, set(required) - installed)) + ")")
        ctx.update(65, "Sicherheitsbackup erstellen")
        operations.backup(server, user, full=False)
        operations.require_stopped(sid)
        ctx.update(95, "Mod installieren", cancellable=False)
        destination.parent.mkdir(exist_ok=True)
        os.chmod(temp, 0o644)
        if os.name != 'nt' and os.geteuid() == 0:
            os.chown(temp, 99, 100)
        os.replace(temp, destination)
        if old and old["filename"] != info["fileName"]:
            safe_path(root(sid), "Server/mods/" + old["filename"]).unlink(missing_ok=True)
            safe_path(root(sid), "Server/mods/" + old["filename"] + ".disabled").unlink(missing_ok=True)
        store.execute("INSERT OR REPLACE INTO managed_mods VALUES(?,?,?,?,?,?,?)",
                      (sid, pid, fid, info["fileName"], metadata["name"], fid, time.time(),))
        return {"name": metadata["name"], "filename": info["fileName"]}
    finally:
        temp.unlink(missing_ok=True)


def check_updates(sid):
    result = []
    for mod in store.query("SELECT * FROM managed_mods WHERE server_id=?", (sid,)):
        options = versions(mod["project_id"])
        stable = [v for v in options if v["release_type"] == 1]
        newest = max(stable, key=lambda v: v["date"] or "") if stable else None
        latest = newest["id"] if newest else mod["file_id"]
        store.execute("UPDATE managed_mods SET latest_id=?,checked=? WHERE server_id=? AND project_id=?",
                      (latest, time.time(), sid, mod["project_id"]))
        result.append(dict(mod, latest_id=latest, checked=time.time()))
    return result
