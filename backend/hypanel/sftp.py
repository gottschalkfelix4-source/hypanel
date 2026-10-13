"""SFTP-only SSH, one server per login. No shell, tunnels or symlinks."""
import errno
import os
import re
import socket
import threading
import paramiko
from fastapi import HTTPException
from . import store, runtime, operations
from .files import root, safe_path, PROTECTED, owned_tree
from .security import access, verify_password, throttle, audit


class Authentication(paramiko.ServerInterface):
    user = None
    sid = None
    def __init__(self, address):
        self.address = address

    def check_auth_password(self, username, password):
        try:
            throttle('sftp:' + self.address)
            name, sid = username.rsplit('+', 1)
            if not re.fullmatch('[a-f0-9]{32}', sid):
                return paramiko.AUTH_FAILED
            user = store.query('SELECT * FROM users WHERE username=? AND disabled=0', (name,), one=True)
            if not user or not verify_password(user['password'], password):
                return paramiko.AUTH_FAILED
            access(user, sid, right='files.read')
            self.user, self.sid = user, sid
            audit(user, 'sftp.login', sid)
            return paramiko.AUTH_SUCCESSFUL
        except Exception:
            return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' and self.user else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


def guarded(method):
    def invoke(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except HTTPException:
            return paramiko.SFTP_PERMISSION_DENIED
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno or errno.EIO)
    return invoke


class Handle(paramiko.SFTPHandle):
    def __init__(self, interface, name, flags, file, writing):
        super().__init__(flags)
        self.interface, self.name, self.writing = interface, name, writing
        self.readfile = self.writefile = file

    @guarded
    def read(self, offset, length):
        self.interface.path(self.name)
        return super().read(offset, length)

    @guarded
    def write(self, offset, data):
        with runtime.lock(self.interface.sid):
            self.interface.path(self.name, True)
            if offset + len(data) > int(os.getenv('HYPANEL_UPLOAD_LIMIT_GB', '16')) * 1024**3:
                raise HTTPException(413, 'Upload-Limit erreicht')
            return super().write(offset, data)

    @guarded
    def stat(self):
        self.interface.path(self.name)
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    def close(self):
        result = super().close()
        if self.writing:
            with runtime.lock(self.interface.sid):
                runtime.EXTERNAL_WRITES[self.interface.sid] = max(0, runtime.EXTERNAL_WRITES.get(self.interface.sid, 1) - 1)
            self.writing = False
        return result


class Interface(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.authentication, self.sid = server, server.sid

    def path(self, path, write=False):
        user = store.query('SELECT * FROM users WHERE id=? AND disabled=0', (self.authentication.user['id'],), one=True)
        if not user or user['password'] != self.authentication.user['password']:
            raise HTTPException(403, 'SFTP-Anmeldung nicht mehr gültig')
        access(user, self.sid, right='files.write' if write else 'files.read')
        if write:
            operations.require_stopped(self.sid)
        relative = os.fsdecode(path).lstrip('/')
        if len(relative) > 1024:
            raise HTTPException(400, 'Dateipfad zu lang')
        return safe_path(root(self.sid), relative)

    @guarded
    def list_folder(self, path):
        result = []
        for item in self.path(path).iterdir():
            if item.is_symlink() or item.name in PROTECTED or item.name.startswith('.hytale'):
                continue
            attr = paramiko.SFTPAttributes.from_stat(item.stat()); attr.filename = item.name; result.append(attr)
        return result

    @guarded
    def stat(self, path):
        return paramiko.SFTPAttributes.from_stat(self.path(path).stat())
    lstat = stat

    @guarded
    def open(self, path, flags, attr):
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        with runtime.lock(self.sid):
            target = self.path(path, writing)
            fd = os.open(target, flags, 0o644)
            if os.name != 'nt' and os.geteuid() == 0 and writing:
                os.fchown(fd, 99, 100)
            mode = 'r+b' if flags & os.O_RDWR else 'wb' if writing else 'rb'
            file = os.fdopen(fd, mode, buffering=0)
            if writing:
                runtime.EXTERNAL_WRITES[self.sid] = runtime.EXTERNAL_WRITES.get(self.sid, 0) + 1
            return Handle(self, path, flags, file, writing)

    @guarded
    def mkdir(self, path, attr):
        with runtime.lock(self.sid):
            target = self.path(path, True); target.mkdir(mode=0o755); owned_tree(target)
        return paramiko.SFTP_OK

    @guarded
    def remove(self, path):
        with runtime.lock(self.sid):
            self.path(path, True).unlink()
        return paramiko.SFTP_OK

    @guarded
    def rmdir(self, path):
        with runtime.lock(self.sid):
            target = self.path(path, True)
            if target in (root(self.sid).resolve(), (root(self.sid)/'Server').resolve()):
                raise HTTPException(403, 'Serverwurzel geschützt')
            target.rmdir()
        return paramiko.SFTP_OK

    @guarded
    def rename(self, oldpath, newpath):
        with runtime.lock(self.sid):
            old, new = self.path(oldpath, True), self.path(newpath, True)
            if old in (root(self.sid).resolve(), (root(self.sid)/'Server').resolve()) or new.exists():
                raise HTTPException(403, 'Geschützte Quelle oder belegtes Ziel')
            if old.is_dir() and any(p.name in PROTECTED or p.name.startswith('.hytale') for p in old.rglob('*')):
                raise HTTPException(403, 'Verzeichnis enthält geschützte Dateien')
            old.rename(new)
        return paramiko.SFTP_OK


def listen(stop, ready=None):
    keypath = store.DATA / 'sftp-host.key'
    if not keypath.exists():
        paramiko.RSAKey.generate(3072).write_private_key_file(str(keypath)); keypath.chmod(0o600)
    key = paramiko.RSAKey.from_private_key_file(str(keypath))
    slots = threading.BoundedSemaphore(32)
    transports = set(); transport_lock = threading.Lock()
    def serve(connection, address):
        transport = paramiko.Transport(connection); transport.auth_timeout=20; transport.banner_timeout=20
        with transport_lock: transports.add(transport)
        try:
            transport.add_server_key(key)
            transport.set_subsystem_handler('sftp', paramiko.SFTPServer, Interface)
            transport.start_server(server=Authentication(address[0]))
            while transport.is_active() and not stop.wait(.5):
                pass
        except Exception:
            pass
        finally:
            transport.close()
            with transport_lock: transports.discard(transport)
            slots.release()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind((os.getenv('HYPANEL_SFTP_BIND', '0.0.0.0'), int(os.getenv('HYPANEL_SFTP_PORT', '2222'))))
        listener.listen(16);listener.settimeout(1)
        if ready: ready.set()
        try:
            while not stop.is_set():
                try:connection,address=listener.accept()
                except socket.timeout:continue
                if not slots.acquire(blocking=False):connection.close();continue
                threading.Thread(target=serve,args=(connection,address),daemon=True).start()
        finally:
            with transport_lock:
                for transport in list(transports):transport.close()
