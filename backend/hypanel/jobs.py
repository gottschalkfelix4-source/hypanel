"""Persistent jobs; cancellation is cooperative and never interrupts a restore commit."""
import json
import threading
import time
import uuid
from fastapi import HTTPException
from . import store, runtime
from .security import audit

SUBMIT_LOCK = threading.Lock()


class Cancelled(Exception):
    pass


class Context:
    def __init__(self, jid):
        self.id = jid
        self.last = 0

    def check(self):
        row = store.query("SELECT cancel,cancellable FROM jobs WHERE id=?", (self.id,), one=True)
        if row and row["cancel"] and row["cancellable"]:
            raise Cancelled()

    def update(self, progress, message, cancellable=True):
        self.check()
        store.execute("UPDATE jobs SET progress=?,message=?,cancellable=? WHERE id=?",
                      (min(99, max(0, progress)), message[:250], cancellable, self.id))


def recover():
    store.execute("UPDATE jobs SET state='failed',finished=?,message='Durch Panelneustart unterbrochen' "
                  "WHERE state IN ('queued','running')", (time.time(),))


def submit(sid, kind, user, operation):
    with SUBMIT_LOCK:
        if store.query("SELECT id FROM jobs WHERE server_id=? AND state IN ('queued','running')", (sid,)):
            raise HTTPException(409, "Für diesen Server läuft bereits ein Auftrag")
        jid = uuid.uuid4().hex
        store.execute("INSERT INTO jobs(id,server_id,kind,user,state,created) VALUES(?,?,?,?,?,?)",
                      (jid, sid, kind, user["username"], "queued", time.time()))
    def run():
        ctx = Context(jid)
        try:
            audit(user, "job." + kind, sid, jid)
            with runtime.lock(sid):
                ctx.check()
                store.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
                result = operation(ctx)
                # Once commit began, a late cancel cannot turn success into failure.
                ctx.check()
                store.execute("UPDATE jobs SET state='done',progress=100,message='Abgeschlossen',finished=?,result=? WHERE id=?",
                              (time.time(), json.dumps(result), jid))
        except Cancelled:
            store.execute("UPDATE jobs SET state='cancelled',message='Abgebrochen',finished=? WHERE id=?", (time.time(), jid))
        except Exception as e:
            # Avoid persisting exception URLs/headers or provider credentials.
            message = e.detail if isinstance(e, HTTPException) and isinstance(e.detail, str) else "Auftrag fehlgeschlagen; Serverprotokoll prüfen"
            store.execute("UPDATE jobs SET state='failed',message=?,finished=? WHERE id=?", (message[:500], time.time(), jid))
    threading.Thread(target=run, name="hypanel-job-" + jid[:8], daemon=True).start()
    return {"job_id": jid}
