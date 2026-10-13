import json
import os
import threading
import time

import docker
from docker.errors import DockerException, NotFound
from fastapi import HTTPException
from .files import root, owned_tree

IMAGE = os.getenv("HYPANEL_RUNNER_IMAGE", "ghcr.io/gottschalkfelix4-source/hypanel:latest")
HOST_DATA = os.getenv("HYPANEL_HOST_DATA", "/mnt/user/appdata/hypanel").rstrip("/")
INSTANCE = os.getenv("HYPANEL_INSTANCE", "default")
LOCKS = {}
LOCKS_LOCK = threading.Lock()
EXTERNAL_WRITES = {}


def lock(sid):
    with LOCKS_LOCK:
        return LOCKS.setdefault(sid, threading.RLock())


def client():
    try:
        return docker.from_env(timeout=60)
    except DockerException:
        raise HTTPException(503, "Docker ist nicht erreichbar")


def container(sid, install=False):
    name = f"hypanel-{INSTANCE}-{'install' if install else 'server'}-{sid}"
    try:
        c = client().containers.get(name)
    except NotFound:
        return None
    labels = c.labels
    if labels.get("io.hypanel.instance") != INSTANCE or labels.get("io.hypanel.server") != sid:
        raise HTTPException(409, "Containername ist von einem fremden Container belegt")
    return c


def active(sid):
    for install in (False, True):
        c = container(sid, install)
        if c and c.status in {"running", "restarting", "paused"}:
            return True
    return False


def state(server, metrics=False):
    sid = server["id"]
    result = dict(server, status="stopped", installed=(root(sid) / "Server/HytaleServer.jar").is_file()
                  and (root(sid) / "Assets.zip").is_file(), memory_bytes=0, cpu_percent=0, exit_code=None)
    try:
        c = container(sid)
        installer = container(sid, True)
        if c:
            result["status"] = c.status
            result["exit_code"] = c.attrs["State"].get("ExitCode")
            if metrics and c.status == "running":
                s = c.stats(stream=False)
                result["memory_bytes"] = s.get("memory_stats", {}).get("usage", 0)
                cpu, prev = s.get("cpu_stats", {}), s.get("precpu_stats", {})
                delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - prev.get("cpu_usage", {}).get("total_usage", 0)
                system = cpu.get("system_cpu_usage", 0) - prev.get("system_cpu_usage", 0)
                result["cpu_percent"] = round(delta / system * cpu.get("online_cpus", 1) * 100, 2) if system > 0 else 0
        if installer:
            result["installation"] = installer.status
    except DockerException:
        result["status"] = "unavailable"
    except HTTPException as e:
        result["status"] = "unavailable"
        result["error"] = e.detail
    return result


def launch(server, install=False):
    sid = server["id"]
    with lock(sid):
        if EXTERNAL_WRITES.get(sid, 0):
            raise HTTPException(409, 'Zuerst laufende SFTP-Uploads abschließen')
        if active(sid):
            raise HTTPException(409, "Server oder Installation läuft bereits")
        if not install and not state(server)["installed"]:
            raise HTTPException(409, "Zuerst Hytale installieren oder Serverdateien importieren")
        old = container(sid, install)
        if old:
            old.remove()
        directory = root(sid)
        directory.mkdir(parents=True, exist_ok=True)
        owned_tree(directory)
        env = {"HYPANEL_MODE": "install" if install else "runner", "SERVER_MEMORY_MB": str(server["memory_mb"]),
               "SERVER_CPUS": str(server["cpus"]), "HYPANEL_TELEMETRY": os.getenv("HYPANEL_TELEMETRY", "true"),
               "SERVER_PORT": str(server["port"]), "PATCHLINE": server["patchline"], "PYTHONUNBUFFERED": "1"}
        c = client().containers.run(
            IMAGE, detach=True, name=f"hypanel-{INSTANCE}-{'install' if install else 'server'}-{sid}",
            environment=env, user="99:100", working_dir="/game", init=True,
            volumes={f"{HOST_DATA}/servers/{sid}": {"bind": "/game", "mode": "rw"},
                     f"{HOST_DATA}/identities/{sid}": {"bind": "/etc/machine-id", "mode": "ro"}},
            ports={} if install else {f"{server['port']}/udp": server["port"]},
            mem_limit=f"{server['memory_mb']}m", nano_cpus=int(server["cpus"] * 1_000_000_000),
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"], read_only=True,
            # QUIC loads a native Netty shared library from /tmp; noexec prevents Hytale boot.
            tmpfs={"/tmp": "rw,exec,nosuid,nodev,size=256m"}, pids_limit=512,
            labels={"io.hypanel.instance": INSTANCE, "io.hypanel.server": sid, "io.hypanel.kind": "install" if install else "server"},
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 3} if server["autorestart"] and not install else {"Name": "no"},
            log_config=docker.types.LogConfig(type="json-file", config={"max-size": "20m", "max-file": "3"}),
        )
        return c.id


def stop(sid, force=False):
    with lock(sid):
        for installer in (True, False):
            c = container(sid, installer)
            if c and c.status in {"running", "restarting", "paused"}:
                c.update(restart_policy={"Name": "no"})
                if force:
                    c.kill()
                else:
                    c.stop(timeout=60)


def command(sid, value):
    if not value or len(value) > 2048 or any(ch in value for ch in "\r\n\x00"):
        raise HTTPException(400, "Ein einzelner Konsolenbefehl ist erforderlich")
    c = container(sid)
    if not c or c.status != "running":
        raise HTTPException(409, "Server läuft nicht")
    code = "import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect('/tmp/hypanel.sock'); s.sendall(sys.argv[1].encode()); s.shutdown(socket.SHUT_WR); print(s.recv(4096).decode()); s.close()"
    result = c.exec_run(["python3", "-c", code, value], user="99:100")
    if result.exit_code or result.output.strip() != b"OK":
        raise HTTPException(409, "Konsole noch nicht bereit oder Server beendet")


def logs(sid, install=False, tail=500):
    c = container(sid, install)
    return c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace") if c else ""
