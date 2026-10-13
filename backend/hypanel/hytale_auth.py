"""Servergebundener Assistent für die offiziellen Hytale-Geräteanmeldungen."""
import json
import re
import time
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit, parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import store, runtime
from .files import root, safe_path
from .security import current_user, access, audit

router = APIRouter(prefix='/api/servers/{sid}/hytale-auth')
User = Annotated[dict, Depends(current_user)]
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
ALLOWED_URLS = {('accounts.hytale.com', '/device'), ('oauth.accounts.hytale.com', '/oauth2/device/verify')}


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0


def device_prompt(logs, since, now=None):
    """Nur aktuelle Codes und fest erlaubte Hytale-Ziele übernehmen."""
    now = time.time() if now is None else now
    lines = []
    for line in ANSI.sub('', logs).splitlines():
        at = timestamp(line.split(' ', 1)[0])
        if at >= since and at <= now + 5:
            lines.append((at, line))
    result = None
    for at, line in lines:
        for raw in re.findall(r'https://[^\s<>"\x1b]+', line):
            try:
                parsed = urlsplit(raw.rstrip('.,);'))
            except ValueError:
                continue
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.netloc != parsed.hostname:
                continue
            if (parsed.hostname, parsed.path) not in ALLOWED_URLS:
                continue
            code = parse_qs(parsed.query).get('user_code', [''])[0]
            if not re.fullmatch(r'[A-Za-z0-9-]{4,32}', code):
                continue
            result = {'code': code, 'url': 'https://' + parsed.hostname + parsed.path + '?' + urlencode({'user_code': code}),
                      'expires_at': at + 900, 'issued_at': at}
        if result and 'expires in' in line.lower():
            match = re.search(r'expires in\s+(\d+)\s+seconds', line, re.I)
            if match:
                result['expires_at'] = result['issued_at'] + min(900, int(match[1]))
        if result and re.search(r'(authorization|authentication|device code).*(expired|denied|failed)|authentication successful', line, re.I):
            result = None
    if result and result['expires_at'] > now:
        result.pop('issued_at')
        return result
    return None


def flow_key(sid, kind):
    return f'hytale-auth:{sid}:{kind}'


def mark(sid, kind, phase, container=None, since=None):
    value = {'phase': phase, 'since': time.time() if since is None else since, 'container': container.id if container else None}
    store.set_setting(flow_key(sid, kind), value)
    return value


def present(sid, name):
    path = safe_path(root(sid), name, credentials=True)
    return path.is_file() and path.stat().st_size > 0


def snapshot(sid, container):
    if not container or container.status != 'running':
        return None
    try:
        path = safe_path(root(sid), 'Server/.hypanel-auth.json', credentials=True)
        if path.stat().st_size > 64 * 1024:
            return None
        data = json.loads(path.read_text())
        if not 0 <= time.time() - data['at'] <= 20 or data['at'] < timestamp(container.attrs.get('State', {}).get('StartedAt')):
            return None
        # Explicit allowlist: no future plugin field can accidentally expose tokens.
        return {'authenticated': data.get('authenticated') is True, 'encrypted': data.get('encrypted') is True,
                'profile': str(data['profile'])[:64] if data.get('profile') else None,
                'profiles': [{'index': int(p['index']), 'name': str(p['name'])[:64]} for p in data.get('profiles', [])[:100]],
                'mode': str(data.get('mode', 'UNKNOWN'))[:32]}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def status(server):
    sid = server['id']
    game = runtime.container(sid)
    installer = runtime.container(sid, True)
    running = game is not None and game.status == 'running'
    installing = installer is not None and installer.status in {'running', 'restarting'}
    state = snapshot(sid, game)
    result = {'server_id': sid, 'installed': runtime.state(server)['installed'], 'running': running,
              'download': {'phase': 'idle', 'saved': present(sid, '.hytale-downloader-credentials.json'), 'device': None},
              'server': {'phase': 'stopped' if not running else 'starting', 'available': state is not None,
                         'authenticated': False, 'saved': present(sid, 'Server/auth.enc'), 'profile': None,
                         'profiles': [], 'encrypted': False, 'device': None}}
    if state:
        result['server'].update(state)
        result['server']['phase'] = 'authenticated' if state['authenticated'] else 'profiles' if state['profiles'] else 'unauthenticated'
    for kind, container, active in [('server', game, running), ('download', installer, installing)]:
        flow = store.setting(flow_key(sid, kind), {})
        matching = container is not None and flow.get('container') == container.id
        if active and matching and flow.get('phase') == 'pending' and not result[kind].get('authenticated'):
            output = runtime.logs(sid, kind == 'download', tail=500)
            prompt = device_prompt(output, flow['since'])
            result[kind]['device'] = prompt
            if prompt:
                result[kind]['phase'] = 'waiting'
            elif time.time() - flow['since'] > 900:
                result[kind]['phase'] = 'expired'
            elif kind == 'download':
                result[kind]['phase'] = 'downloading' if result[kind]['saved'] else 'starting'
            elif not result[kind].get('profiles'):
                result[kind]['phase'] = 'pending'
            if kind == 'server' and not result[kind].get('profiles'):
                recent = [line for line in ANSI.sub('', output).splitlines()
                          if timestamp(line.split(' ', 1)[0]) >= flow['since']]
                if any(re.search(r'authentication failed|authorization.*(?:denied|expired)|device.*(?:timed out|failed|expired)', line, re.I) for line in recent):
                    result[kind].update(phase='failed', device=None)
        if kind == 'download' and installing and not matching:
            result[kind]['phase'] = 'downloading'
        if kind == 'download' and installer and not installing and matching:
            result[kind]['phase'] = ('cancelled' if flow.get('phase') == 'cancelled' else
                                    'complete' if installer.attrs.get('State', {}).get('ExitCode') == 0 and (result['installed'] or result[kind]['saved']) else 'failed')
    return result


@router.get('')
def read_status(sid: str, user: User):
    return status(access(user, sid, right='auth.manage'))


class Action(BaseModel):
    action: Literal['install', 'download-login', 'start', 'restart', 'login', 'select', 'cancel', 'logout', 'forget-download', 'save']
    target: Literal['server', 'download'] = 'server'
    profile: int = Field(default=1, ge=1, le=100)


@router.post('')
def change(sid: str, body: Action, user: User):
    server = access(user, sid, right='auth.manage')
    with runtime.lock(sid):
        if store.query("SELECT id FROM jobs WHERE server_id=? AND state IN ('queued','running')", (sid,)):
            raise HTTPException(409, 'Zuerst den laufenden Serverauftrag abschließen')
        if body.action in {'install', 'download-login', 'start', 'restart'}:
            access(user, sid, right='server.power')
            if body.action in {'install', 'download-login'}:
                if body.action == 'install' and runtime.state(server)['installed']:
                    raise HTTPException(409, 'Serverdateien sind bereits installiert')
                since = time.time()
                runtime.launch(server, install=True, auth_only=body.action == 'download-login')
                mark(sid, 'download', 'pending', runtime.container(sid, True), since=since)
            else:
                if body.action == 'restart':
                    runtime.stop(sid)
                runtime.launch(server)
                mark(sid, 'server', 'idle', runtime.container(sid))
        elif body.action == 'forget-download':
            installer = runtime.container(sid, True)
            if installer and installer.status in {'running', 'restarting'}:
                raise HTTPException(409, 'Zuerst den Download abbrechen')
            safe_path(root(sid), '.hytale-downloader-credentials.json', credentials=True).unlink(missing_ok=True)
            mark(sid, 'download', 'idle')
        elif body.action == 'logout':
            game = runtime.container(sid)
            if game and game.status == 'running':
                runtime.command(sid, 'auth cancel')
                runtime.command(sid, 'auth logout')
            else:
                if runtime.active(sid):
                    raise HTTPException(409, 'Zuerst den laufenden Vorgang beenden')
                for name in ('Server/auth.enc', 'Server/auth.key', 'Server/.hytale-server-credentials.json'):
                    safe_path(root(sid), name, credentials=True).unlink(missing_ok=True)
            mark(sid, 'server', 'logged_out', game)
        elif body.action == 'cancel':
            if body.target == 'download':
                access(user, sid, right='server.power')
                installer = runtime.container(sid, True)
                if installer and installer.status == 'running':
                    installer.stop(timeout=10)
                mark(sid, 'download', 'cancelled', installer)
            else:
                runtime.command(sid, 'auth cancel')
                mark(sid, 'server', 'cancelled', runtime.container(sid))
        elif body.action == 'login':
            state = snapshot(sid, runtime.container(sid))
            if state and state['authenticated']:
                raise HTTPException(409, 'Vor einem Accountwechsel diesen Server abmelden')
            runtime.command(sid, 'auth cancel')
            runtime.command(sid, 'auth persistence Encrypted')
            mark(sid, 'server', 'pending', runtime.container(sid))
            runtime.command(sid, 'auth login device')
        elif body.action == 'select':
            state = snapshot(sid, runtime.container(sid))
            if not state or body.profile not in [p['index'] for p in state['profiles']]:
                raise HTTPException(409, 'Profilauswahl ist nicht mehr aktuell')
            runtime.command(sid, 'auth select ' + str(body.profile))
        elif body.action == 'save':
            runtime.command(sid, 'auth persistence Encrypted')
        audit(user, 'hytale-auth.' + body.action, sid, body.target)
    return status(server)


def mask_device_codes(value):
    value = re.sub(r'(user_code=)[^\s&]+', r'\1[CODE VERBORGEN]', value, flags=re.I)
    return re.sub(r'((?:enter|verification|device|user)\s+code\s*:\s*)[A-Za-z0-9-]+', r'\1[CODE VERBORGEN]', value, flags=re.I)
