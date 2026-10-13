import json
import os
import shutil
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import HTTPException
from . import store, runtime
from .files import root, create_zip, extract_zip, owned_tree, safe_path
from .security import audit


def require_stopped(sid):
    if runtime.active(sid):
        raise HTTPException(409, "Server und Installation müssen zuerst gestoppt werden")


def backup(server, user="scheduler", full=False):
    sid = server["id"]
    with runtime.lock(sid):
        if (installer := runtime.container(sid, True)) and installer.status == "running":
            raise HTTPException(409, "Installation läuft")
        running = (c := runtime.container(sid)) and c.status == "running"
        directory = store.DATA / "backups" / sid
        directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".zip"
        output = directory / name
        temporary = output.with_suffix(".partial")
        try:
            if running:
                runtime.stop(sid)
            create_zip(root(sid), temporary, binaries=full)
            os.replace(temporary, output)
            audit(user, "backup.create", sid, name)
            retention = store.setting("backup_retention", 10)
            backups = sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in backups[retention:]:
                p.unlink()
        finally:
            temporary.unlink(missing_ok=True)
            if running:
                runtime.launch(server)
        return {"name": name, "size": output.stat().st_size, "full": full}


def restore(server, name, user):
    sid = server["id"]
    with runtime.lock(sid):
        require_stopped(sid)
        archive = safe_path(store.DATA / "backups" / sid, name)
        if not archive.is_file() or archive.suffix != ".zip":
            raise HTTPException(404, "Backup nicht gefunden")
        staging = store.DATA / "uploads" / uuid.uuid4().hex
        staging.mkdir()
        try:
            extract_zip(archive, staging)
            # Replace save/config payload rather than merging stale world chunks.
            backup(server, user, full=True)
            for item in staging.iterdir():
                target = safe_path(root(sid), item.name)
                if item.is_dir() and item.name == "Server":
                    for child in item.iterdir():
                        destination = safe_path(root(sid), 'Server/' + child.name)
                        if destination.is_dir():
                            shutil.rmtree(destination)
                        elif destination.exists():
                            destination.unlink()
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(child), destination)
                else:
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                    shutil.move(str(item), target)
            owned_tree(root(sid))
            audit(user, "backup.restore", sid, name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def next_run(expression, timezone, base=None):
    try:
        zone = ZoneInfo(timezone)
        if len(expression.split()) != 5:
            raise ValueError()
        return croniter(expression, datetime.fromtimestamp(base or time.time(), zone)).get_next(datetime).timestamp()
    except Exception:
        raise HTTPException(400, "Ungültiger Cron-Ausdruck oder Zeitzone")


def scheduled_action(job):
    server = store.query("SELECT * FROM servers WHERE id=?", (job["server_id"],), one=True)
    with runtime.lock(server["id"]):
        action = job["action"]
        if action == "backup":
            backup(server)
        elif action == "start":
            runtime.launch(server)
        elif action == "stop":
            runtime.stop(server["id"])
        elif action == "restart":
            runtime.stop(server["id"])
            runtime.launch(server)
        elif action == "command":
            runtime.command(server["id"], job["command"])
        elif action == "update":
            require_stopped(server["id"])
            backup(server, full=True)
            runtime.launch(server, install=True)
        audit("scheduler", "schedule." + action, server["id"], job["name"])


def scheduler(stop_event):
    while not stop_event.wait(10):
        for job in store.query("SELECT * FROM schedules WHERE enabled=1 AND next_run<=?", (time.time(),)):
            # Claim before execution; overdue tasks run once, never replay in a loop.
            store.execute("UPDATE schedules SET next_run=?,last_run=? WHERE id=?",
                          (next_run(job["cron"], job["timezone"]), time.time(), job["id"]))
            try:
                scheduled_action(job)
                result = "OK"
            except Exception as e:
                result = str(getattr(e, "detail", type(e).__name__))[:500]
                audit("scheduler", "schedule.failed", job["server_id"], result)
            store.execute("UPDATE schedules SET last_result=? WHERE id=?", (result, job["id"]))
