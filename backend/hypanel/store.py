import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA = Path(os.getenv("HYPANEL_DATA", "/data")).resolve()


@contextmanager
def db():
    con = sqlite3.connect(DATA / "panel.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    for name in ("servers", "backups", "uploads", "identities"):
        (DATA / name).mkdir(exist_ok=True)
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password TEXT NOT NULL, role TEXT NOT NULL, disabled INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
          csrf TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS servers(id TEXT PRIMARY KEY, name TEXT NOT NULL, port INTEGER UNIQUE NOT NULL,
          memory_mb INTEGER NOT NULL, cpus REAL NOT NULL, autostart INTEGER DEFAULT 0,
          autorestart INTEGER DEFAULT 0, patchline TEXT DEFAULT 'release', created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS access(server_id TEXT REFERENCES servers(id) ON DELETE CASCADE,
          user_id TEXT REFERENCES users(id) ON DELETE CASCADE, permission TEXT NOT NULL,
          PRIMARY KEY(server_id,user_id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL, user TEXT NOT NULL,
          action TEXT NOT NULL, server_id TEXT, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, server_id TEXT REFERENCES servers(id) ON DELETE CASCADE,
          name TEXT NOT NULL, cron TEXT NOT NULL, timezone TEXT NOT NULL, action TEXT NOT NULL,
          command TEXT NOT NULL DEFAULT '', enabled INTEGER DEFAULT 1, next_run REAL NOT NULL,
          last_run REAL, last_result TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens(id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL,
          user_id TEXT REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL,
          created REAL NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS metrics(server_id TEXT NOT NULL, at REAL NOT NULL,
          status TEXT NOT NULL, cpu REAL, memory REAL, players INTEGER, tps REAL,
          PRIMARY KEY(server_id,at));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, server_id TEXT, kind TEXT NOT NULL,
          user TEXT NOT NULL, state TEXT NOT NULL, progress REAL DEFAULT 0, message TEXT DEFAULT '',
          created REAL NOT NULL, finished REAL, cancel INTEGER DEFAULT 0, cancellable INTEGER DEFAULT 1,
          result TEXT);
        CREATE TABLE IF NOT EXISTS known_players(server_id TEXT NOT NULL, uuid TEXT NOT NULL,
          name TEXT NOT NULL, last_seen REAL NOT NULL, PRIMARY KEY(server_id,uuid));
        CREATE TABLE IF NOT EXISTS managed_mods(server_id TEXT NOT NULL, project_id INTEGER NOT NULL,
          file_id INTEGER NOT NULL, filename TEXT NOT NULL, name TEXT NOT NULL,
          latest_id INTEGER, checked REAL, PRIMARY KEY(server_id,project_id));
        ''')


def query(sql, args=(), one=False):
    with db() as c:
        rows = c.execute(sql, args).fetchall()
    return (dict(rows[0]) if rows else None) if one else [dict(r) for r in rows]


def execute(sql, args=()):
    with db() as c:
        c.execute(sql, args)


def setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return json.loads(row["value"]) if row else default


def set_setting(key, value):
    execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))
