import os
import shutil
import stat
import zipfile
from pathlib import Path

from fastapi import HTTPException
from .store import DATA

PROTECTED = {"auth.enc", "auth.key", ".machine-id", ".hytale-downloader-credentials.json", ".hytale-server-credentials.json"}
MAX_ARCHIVE_BYTES = 32 * 1024**3


def root(sid):
    return DATA / "servers" / sid


def safe_path(base, name, credentials=False):
    if "\\" in name or "\x00" in name or ":" in name:
        raise HTTPException(400, "Ungültiger Dateipfad")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "Ungültiger Dateipfad")
    if not credentials and any(p in PROTECTED or p.startswith(".hytale") for p in relative.parts):
        raise HTTPException(403, "Authentifizierungsdateien sind geschützt")
    base = base.resolve()
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(400, "Symbolische Links sind nicht erlaubt")
    path = current.resolve()
    if not path.is_relative_to(base):
        raise HTTPException(400, "Pfad außerhalb des Serververzeichnisses")
    return path


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".hypanel-tmp")
    try:
        with temporary.open("wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        if os.name != "nt" and os.geteuid() == 0:
            os.chown(path, 99, 100)
    finally:
        temporary.unlink(missing_ok=True)


def owned_tree(path):
    if os.name != "nt" and os.geteuid() == 0:
        for p in [path, *path.rglob("*")]:
            if not p.is_symlink():
                os.chown(p, 99, 100)


def extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as z:
        total = 0
        for info in z.infolist():
            total += info.file_size
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or total > MAX_ARCHIVE_BYTES:
                raise HTTPException(400, "Archiv enthält Links oder überschreitet 32 GiB")
            safe_path(destination, info.orig_filename, credentials=True)
            safe_path(destination, info.filename, credentials=True)
        for info in z.infolist():
            target = safe_path(destination, info.filename, credentials=True)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                os.chmod(target, 0o600 if target.name in PROTECTED else 0o644)


def create_zip(source, output, binaries=False):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in source.rglob("*"):
            rel = p.relative_to(source)
            if p.is_symlink():
                continue
            if any(part in PROTECTED or part.startswith(".hytale") for part in rel.parts):
                continue
            if any(part in {"logs", ".cache", "updater", "backups"} for part in rel.parts):
                continue
            if not binaries and p.name in {"Assets.zip", "HytaleServer.jar", "HytaleServer.aot"}:
                continue
            z.write(p, rel.as_posix())
