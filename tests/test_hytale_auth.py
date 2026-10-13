import json
import time
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from hypanel import hytale_auth as auth, runtime, store


def line(at, value):
    return datetime.fromtimestamp(at, timezone.utc).isoformat() + ' ' + value


def game(sid):
    return SimpleNamespace(id='container-'+sid, status='running', attrs={'State':{'StartedAt':'2026-01-01T00:00:00Z'}})


def test_device_codes_are_fresh_expiring_and_official_only():
    now=time.time()
    value=line(now-2,'Visit https://accounts.hytale.com/device?user_code=ABCD-1234')
    result=auth.device_prompt(value,now-3,now)
    assert result['code']=='ABCD-1234' and result['expires_at']==pytest.approx(now-2+900,abs=.001)
    assert auth.device_prompt(value,now-1,now) is None
    assert auth.device_prompt(value,now-3,now+901) is None
    assert auth.device_prompt(value+'\n'+line(now-1,'Authentication failed'),now-3,now) is None
    for target in ('https://evil.test/device', 'https://accounts.hytale.com.evil.test/device',
                   'https://user@accounts.hytale.com/device', 'https://accounts.hytale.com:443/device',
                   'https://accounts.hytale.com/untrusted'):
        assert auth.device_prompt(line(now,target+'?user_code=ABCD-1234'),now-1,now) is None
    assert auth.device_prompt(line(now,'https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=ABCD-1234&evil=ignored'),now-1,now)['url'].endswith('user_code=ABCD-1234')


def test_status_never_exposes_tokens_or_accepts_stale_plugin_snapshot(server,monkeypatch):
    c,directory,sid=server
    container=game(sid)
    monkeypatch.setattr(runtime,'container',lambda _,install=False:None if install else container)
    path=directory/'servers'/sid/'Server/.hypanel-auth.json'
    data={'at':time.time(),'authenticated':True,'encrypted':True,'profile':'Friend','profiles':[],
          'access_token':'do-not-return','refresh_token':'never-return'}
    path.write_text(json.dumps(data))
    r=c.get(f'/api/servers/{sid}/hytale-auth')
    assert r.json()['server']['profile']=='Friend'
    assert 'do-not-return' not in r.text and 'never-return' not in r.text
    data['at']-=30;path.write_text(json.dumps(data))
    assert c.get(f'/api/servers/{sid}/hytale-auth').json()['server']['available'] is False
    assert c.get(f'/api/servers/{sid}/files/content',params={'path':'Server/.hypanel-auth.json'}).status_code==403


def test_friend_can_authenticate_only_assigned_server_without_panel_admin(server,monkeypatch):
    c,directory,sid=server
    other=c.post('/api/servers',json={'name':'Other','port':5521}).json()['id']
    uid=c.post('/api/users',json={'username':'friend','password':'friend-password-long'}).json()['id']
    c.put(f'/api/servers/{sid}/access',json={'user_id':uid,'permission':'custom','rights':['auth.manage','server.power']})
    c.post('/api/logout');r=c.post('/api/login',json={'username':'friend','password':'friend-password-long'})
    c.headers['X-CSRF-Token']=r.json()['csrf']
    monkeypatch.setattr(runtime,'container',lambda s,install=False:None if install else game(s))
    sent=[];monkeypatch.setattr(runtime,'command',lambda s,v:sent.append((s,v)))
    monkeypatch.setattr(runtime,'logs',lambda *a,**k:'')
    assert c.get(f'/api/servers/{other}/hytale-auth').status_code==403
    assert c.post(f'/api/servers/{other}/hytale-auth',json={'action':'login'}).status_code==403
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'login'}).status_code==200
    assert sent==[(sid,'auth cancel'),(sid,'auth persistence Encrypted'),(sid,'auth login device')]
    assert store.setting(auth.flow_key(other,'server')) is None


def test_offline_logout_and_download_forget_touch_only_selected_server(server):
    c,directory,sid=server
    other=c.post('/api/servers',json={'name':'Other','port':5521}).json()['id']
    for s in (sid,other):
        base=directory/'servers'/s
        (base/'Server/auth.enc').write_bytes(b'separate-'+s.encode())
        (base/'.hytale-downloader-credentials.json').write_text('private-'+s)
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'logout'}).status_code==200
    assert not (directory/'servers'/sid/'Server/auth.enc').exists()
    assert (directory/'servers'/other/'Server/auth.enc').exists()
    assert (directory/'servers'/sid/'.hytale-downloader-credentials.json').exists()
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'forget-download'}).status_code==200
    assert (directory/'servers'/other/'.hytale-downloader-credentials.json').read_text()=='private-'+other


def test_old_container_device_flow_cannot_reappear_after_restart(server,monkeypatch):
    c,_,sid=server
    now=time.time()
    store.set_setting(auth.flow_key(sid,'server'),{'container':'old-container','phase':'pending','since':now-5})
    monkeypatch.setattr(runtime,'container',lambda s,install=False:None if install else game(s))
    monkeypatch.setattr(runtime,'logs',lambda *a,**k:line(now-1,'https://accounts.hytale.com/device?user_code=ABCD-1234'))
    assert c.get(f'/api/servers/{sid}/hytale-auth').json()['server']['device'] is None


def test_profile_selection_must_match_pending_profile_and_sends_no_arbitrary_command(server,monkeypatch):
    c,directory,sid=server
    monkeypatch.setattr(runtime,'container',lambda s,install=False:None if install else game(s))
    (directory/'servers'/sid/'Server/.hypanel-auth.json').write_text(json.dumps(
        {'at':time.time(),'authenticated':False,'profiles':[{'index':2,'name':'Friend'}]}))
    sent=[];monkeypatch.setattr(runtime,'command',lambda s,v:sent.append(v))
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'select','profile':1}).status_code==409
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'select','profile':2}).status_code==200
    assert sent==['auth select 2']
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'select','profile':'2;stop'}).status_code==422


def test_console_only_user_cannot_start_auth_and_sees_no_device_code(server,monkeypatch):
    c,_,sid=server
    uid=c.post('/api/users',json={'username':'reader','password':'reader-password-long'}).json()['id']
    c.put(f'/api/servers/{sid}/access',json={'user_id':uid,'permission':'custom','rights':['console.read','console.write']})
    c.post('/api/logout');r=c.post('/api/login',json={'username':'reader','password':'reader-password-long'})
    c.headers['X-CSRF-Token']=r.json()['csrf']
    monkeypatch.setattr(runtime,'logs',lambda *a,**k:'Enter code: ABCD-1234\nhttps://accounts.hytale.com/device?user_code=ABCD-1234')
    assert 'ABCD-1234' not in c.get(f'/api/servers/{sid}/logs').text
    assert c.post(f'/api/servers/{sid}/command',json={'command':' /auth login device'}).status_code==403
    assert c.get(f'/api/servers/{sid}/hytale-auth').status_code==403


def test_initial_installer_is_server_scoped_and_busy_jobs_prevent_auth_changes(server,monkeypatch):
    c,_,sid=server
    launched=[]
    monkeypatch.setattr(runtime,'state',lambda s:dict(s,installed=False))
    monkeypatch.setattr(runtime,'launch',lambda s,install=False,**kwargs:launched.append((s['id'],install)))
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'install'}).status_code==200
    assert launched==[(sid,True)]
    store.execute("INSERT INTO jobs(id,server_id,kind,user,state,created) VALUES(?,?,?,?,?,?)",('busy',sid,'backup','admin','running',time.time()))
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'logout'}).status_code==409


def test_cancel_hides_code_and_auth_failure_is_retryable(server,monkeypatch):
    c,_,sid=server
    container=game(sid)
    monkeypatch.setattr(runtime,'container',lambda s,install=False:None if install else container)
    monkeypatch.setattr(runtime,'command',lambda *a:None)
    auth.mark(sid,'server','pending',container)
    output=line(time.time(),'https://accounts.hytale.com/device?user_code=ABCD-1234')
    monkeypatch.setattr(runtime,'logs',lambda *a,**k:output)
    assert c.get(f'/api/servers/{sid}/hytale-auth').json()['server']['device']['code']=='ABCD-1234'
    output+='\n'+line(time.time(),'Authentication failed')
    assert c.get(f'/api/servers/{sid}/hytale-auth').json()['server']['phase']=='failed'
    assert c.post(f'/api/servers/{sid}/hytale-auth',json={'action':'cancel'}).json()['server']['device'] is None
