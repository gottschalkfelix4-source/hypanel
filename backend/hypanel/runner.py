"""Entrypoint for isolated game containers; no Docker socket or panel credentials."""
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from .files import extract_zip

GAME = Path("/game")


def apply_staged():
    staging = GAME / "updater/staging"
    if not (staging / "Server/HytaleServer.jar").is_file():
        return
    print("[HyPanel] Wende vorbereitete Hytale-Aktualisierung an.", flush=True)
    for relative in ("Server/HytaleServer.jar", "Server/HytaleServer.aot", "Assets.zip", "start.sh", "start.bat"):
        src = staging / relative
        if src.is_file():
            shutil.copy2(src, GAME / relative)
    licenses = staging / "Server/Licenses"
    if licenses.is_dir():
        shutil.copytree(licenses, GAME / "Server/Licenses", dirs_exist_ok=True)
    shutil.rmtree(staging)


def install():
    archive = GAME / ".download.zip"
    extracted = GAME / ".download-staging"
    print("[HyPanel] Offizieller Hytale-Downloader. Gerätecode hier in der Konsole bestätigen.", flush=True)
    result = subprocess.call(["/opt/hytale-downloader", "-download-path", str(archive),
                              "-credentials-path", str(GAME / ".hytale-downloader-credentials.json"),
                              "-patchline", os.environ.get("PATCHLINE", "release")], cwd=GAME)
    if result:
        raise SystemExit(result)
    extracted.mkdir(exist_ok=True)
    try:
        extract_zip(archive, extracted)
        jars = list(extracted.rglob("HytaleServer.jar"))
        assets = list(extracted.rglob("Assets.zip"))
        if len(jars) != 1 or len(assets) != 1:
            raise RuntimeError("Download enthält kein eindeutiges Hytale-Serverpaket")
        server = GAME / "Server"
        server.mkdir(exist_ok=True)
        shutil.copy2(jars[0], server / "HytaleServer.jar")
        if (jars[0].parent / "HytaleServer.aot").is_file():
            shutil.copy2(jars[0].parent / "HytaleServer.aot", server / "HytaleServer.aot")
        shutil.copy2(assets[0], GAME / "Assets.zip")
        for name in ("start.sh", "start.bat"):
            paths = list(extracted.rglob(name))
            if paths:
                shutil.copy2(paths[0], GAME / name)
        print("[HyPanel] Installation abgeschlossen. Konfiguration, Welten und Mods wurden erhalten.", flush=True)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(extracted, ignore_errors=True)


def run():
    shutdown = threading.Event()
    writer_lock = threading.Lock()
    process = None

    def send(command):
        with writer_lock:
            if process is None or process.poll() is not None:
                return False
            try:
                process.stdin.write(command.rstrip("\n") + "\n")
                process.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                return False

    def terminate(*_):
        shutdown.set()
        send("stop")

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    address = "/tmp/hypanel.sock"
    Path(address).unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(address)
    os.chmod(address, 0o600)
    listener.listen(5)
    listener.settimeout(1)

    def console():
        while not shutdown.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(5)
                data = bytearray()
                try:
                    while len(data) <= 2048:
                        chunk = connection.recv(2049)
                        if not chunk:
                            break
                        data.extend(chunk)
                    value = data.decode("utf-8")
                    valid = 0 < len(value) <= 2048 and not any(c in value for c in "\r\n\x00")
                    connection.sendall(b"OK" if valid and send(value) else b"ERROR")
                except (OSError, UnicodeDecodeError):
                    pass

    threading.Thread(target=console, daemon=True).start()
    heap = max(1024, int(os.environ.get("SERVER_MEMORY_MB", "4096")) - 1024)
    while not shutdown.is_set():
        apply_staged()
        argv = ["java", f"-Xms512m", f"-Xmx{heap}m", "-XX:+UseG1GC"]
        if (GAME / "Server/HytaleServer.aot").is_file():
            argv.append("-XX:AOTCache=HytaleServer.aot")
        argv.extend(["-jar", "HytaleServer.jar", "--assets", "../Assets.zip", "--bind",
                     "0.0.0.0:" + os.environ.get("SERVER_PORT", "5520")])
        print("[HyPanel] Starte Hytale mit Java 25.", flush=True)
        process = subprocess.Popen(argv, cwd=GAME / "Server", stdin=subprocess.PIPE, text=True)
        code = process.wait()
        if code != 8 or shutdown.is_set():
            shutdown.set()
            return code
        print("[HyPanel] Hytale hat einen Update-Neustart angefordert.", flush=True)
        time.sleep(1)
    return 0


if __name__ == "__main__":
    if os.environ.get("HYPANEL_MODE") == "install":
        install()
    else:
        raise SystemExit(run())
