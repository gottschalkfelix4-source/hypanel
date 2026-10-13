"""Docker resource samples and measured Hytale world tick progression."""
import json
import math
import time
import psutil
from . import store, runtime
from .files import root, safe_path


def telemetry(sid, running=True):
    if not running:
        return {"available": False, "players": [], "worlds": [], "tps": None}
    try:
        path = safe_path(root(sid), "Server/.hypanel-telemetry.json", credentials=True)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError()
        data = json.loads(path.read_text())
        age = time.time() - data["at"]
        if not 0 <= age <= 20 or not isinstance(data["players"], list):
            raise ValueError()
        values = [w["tps"] for w in data["worlds"] if isinstance(w.get("tps"), (int, float))
                  and math.isfinite(w["tps"]) and 0 <= w["tps"] <= 200]
        return dict(data, available=True, tps=round(min(values), 2) if values else None)
    except (OSError, ValueError, TypeError, KeyError):
        return {"available": False, "players": [], "worlds": [], "tps": None}


def host():
    memory = psutil.virtual_memory()
    # FastAPI uses different worker threads. An initial nonblocking read in each
    # thread returns 0, so measure a short real interval for the host overview.
    return {"cpu_percent": psutil.cpu_percent(interval=0.1), "memory_used": memory.used, "memory_total": memory.total,
            "uptime": max(0, time.time() - psutil.boot_time())}


def sample():
    now = time.time()
    for server in store.query("SELECT * FROM servers"):
        state = runtime.state(server, metrics=True)
        data = telemetry(server["id"], state["status"] == "running")
        # Missing readings are NULL, never a fabricated zero or target tick rate.
        available = state["status"] != "unavailable"
        store.execute("INSERT INTO metrics VALUES(?,?,?,?,?,?,?)",
                      (server["id"], now, state["status"], state["cpu_percent"] / server["cpus"] if available else None,
                       state["memory_bytes"] / 1024**2 if available else None,
                       len(data["players"]) if data["available"] else None, data["tps"]))
        for p in data["players"]:
            if isinstance(p.get("uuid"), str) and isinstance(p.get("name"), str):
                store.execute("INSERT OR REPLACE INTO known_players VALUES(?,?,?,?)",
                              (server["id"], p["uuid"][:64], p["name"][:64], now))
    store.execute("DELETE FROM metrics WHERE at<?", (now - 72 * 3600,))
    store.execute("DELETE FROM jobs WHERE finished<?", (now - 30 * 86400,))


def collector(stop):
    while not stop.wait(10):
        try:
            sample()
        except Exception:
            # Docker outages must not terminate background collection.
            pass


def history(sid, hours=1):
    rows = store.query("SELECT * FROM metrics WHERE server_id=? AND at>? ORDER BY at",
                       (sid, time.time() - hours * 3600))
    # Aggregate evenly sized buckets, respecting missing telemetry and outage gaps.
    size = max(1, math.ceil(len(rows) / 360))
    result = []
    for i in range(0, len(rows), size):
        chunk = rows[i:i + size]
        point = dict(chunk[-1])
        for key in ("cpu", "memory", "players", "tps"):
            values = [p[key] for p in chunk if p[key] is not None]
            point[key] = sum(values) / len(values) if values and all(p["status"] == point["status"] for p in chunk) else None
        result.append(point)
    return result
