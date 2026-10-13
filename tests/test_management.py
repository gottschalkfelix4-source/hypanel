import json
import time
import threading
import pytest
from fastapi import HTTPException
from hypanel import store, metrics, operations, runtime, jobs, providers


def test_telemetry_expires_and_missing_tps_stays_null(server):
    _, directory, sid = server
    path = directory / 'servers' / sid / 'Server/.hypanel-telemetry.json'
    payload = {'at':time.time(),'players':[], 'worlds':[{'name':'default','tps':None}]}
    path.write_text(json.dumps(payload))
    assert metrics.telemetry(sid)['available'] is True
    assert metrics.telemetry(sid)['tps'] is None
    payload['at'] -= 30
    path.write_text(json.dumps(payload))
    assert metrics.telemetry(sid)['available'] is False
    assert metrics.telemetry(sid, running=False)['tps'] is None


def test_measured_world_tps_and_history_survive_restart(server):
    c, directory, sid = server
    (directory / 'servers' / sid / 'Server/.hypanel-telemetry.json').write_text(json.dumps(
        {'at':time.time(),'players':[{'uuid':'id','name':'Felix'}], 'worlds':[{'tps':30.1},{'tps':24.5}]}))
    assert metrics.telemetry(sid)['tps'] == 24.5
    store.execute('INSERT INTO metrics VALUES(?,?,?,?,?,?,?)', (sid,time.time(),'running',50,2000,1,24.5))
    store.init()
    history=c.get(f'/api/servers/{sid}/metrics/history').json()
    assert history[0]['tps'] == 24.5


def test_config_form_preserves_unknown_fields_and_validates(server):
    c, directory, sid = server
    path=directory/'servers'/sid/'Server/config.json'
    path.write_text('{"ServerName":"Alt","Custom":{"keep":true}}')
    prefix=f'/api/servers/{sid}/config-form'
    assert c.put(prefix,json={'values':{'MaxPlayers':-1}}).status_code==400
    assert c.put(prefix,json={'values':{'Secret':'x'}}).status_code==400
    assert c.put(prefix,json={'values':{'ServerName':'Neu','MaxPlayers':30}}).status_code==200
    result=json.loads(path.read_text())
    assert result['Custom']=={'keep':True} and result['ServerName']=='Neu'


def test_quota_failure_preserves_existing_backups_and_running_server(server,monkeypatch):
    c, directory, sid = server
    path=directory/'servers'/sid/'Server/test.bin';path.write_bytes(b'content')
    policy={'retention':10,'quota_bytes':10,'global_quota_bytes':10}
    monkeypatch.setattr(operations,'backup_policy',lambda _:policy)
    stopped=[];monkeypatch.setattr(runtime,'stop',lambda _:stopped.append(True))
    response=c.post(f'/api/servers/{sid}/backups')
    assert response.status_code==409 and stopped==[]
    assert not list((directory/'backups'/sid).glob('*.zip'))


def test_backup_job_persists_progress_and_result(server):
    c, directory, sid = server
    response=c.post(f'/api/servers/{sid}/tasks',json={'action':'backup'})
    assert response.status_code==202
    jid=response.json()['job_id']
    for _ in range(100):
        row=c.get('/api/jobs/'+jid).json()
        if row['state'] not in {'queued','running'}:break
        time.sleep(.02)
    assert row['state']=='done' and row['progress']==100
    assert (directory/'backups'/sid/json.loads(row['result'])['name']).exists()


def test_job_cancel_and_concurrent_submission(server):
    c, _, sid=server
    started=threading.Event();release=threading.Event()
    def action(ctx):
        started.set();release.wait(2);ctx.check()
    user={'username':'admin'}
    jid=jobs.submit(sid,'test',user,action)['job_id'];started.wait(1)
    with pytest.raises(HTTPException) as error:jobs.submit(sid,'second',user,action)
    assert error.value.status_code==409
    assert c.post('/api/jobs/'+jid+'/cancel').status_code==200
    release.set()
    for _ in range(100):
        row=c.get('/api/jobs/'+jid).json()
        if row['state']=='cancelled':break
        time.sleep(.02)
    assert row['state']=='cancelled'


def test_granular_rights_and_delegation_cannot_escalate(server,monkeypatch):
    c, _, sid=server
    uid=c.post('/api/users',json={'username':'member','password':'secure-member-password'}).json()['id']
    peer=c.post('/api/users',json={'username':'peer','password':'secure-member-password'}).json()['id']
    c.put(f'/api/servers/{sid}/access',json={'user_id':uid,'permission':'custom','rights':['console.read','server.power','access.write']})
    c.post('/api/logout');r=c.post('/api/login',json={'username':'member','password':'secure-member-password'})
    c.headers['X-CSRF-Token']=r.json()['csrf']
    monkeypatch.setattr(runtime,'launch',lambda *a,**k:None)
    assert c.post(f'/api/servers/{sid}/power/start').status_code==200
    assert c.post(f'/api/servers/{sid}/command',json={'command':'help'}).status_code==403
    assert c.get(f'/api/servers/{sid}/files').status_code==403
    assert c.put(f'/api/servers/{sid}/access',json={'user_id':peer,'permission':'operator'}).status_code==403
    assert c.put(f'/api/servers/{sid}/access',json={'user_id':peer,'permission':'custom','rights':['console.read']}).status_code==200


def test_curseforge_rejects_other_game_and_download_hosts(server,monkeypatch):
    monkeypatch.setattr(providers,'game',lambda:999)
    monkeypatch.setattr(providers,'request',lambda *a,**k:{'data':{'gameId':432}})
    with pytest.raises(HTTPException):providers.project(1)
    for url in ['http://edge.forgecdn.net/a','https://127.0.0.1/a','https://forgecdn.net.evil.test/a','https://user@edge.forgecdn.net/a']:
        with pytest.raises(HTTPException):providers.allowed_download(url)
    providers.allowed_download('https://edge.forgecdn.net/files/a.jar')


def test_key_never_returned_in_provider_settings(authenticated):
    c,_=authenticated
    store.set_setting('curseforge_key','private-test-value')
    response=c.get('/api/providers/curseforge')
    assert response.json()=={'configured':True}
    assert 'private-test-value' not in response.text


def test_panel_snapshot_and_zip_extraction_are_guarded(server):
    c,directory,sid=server
    response=c.post('/api/panel-backups');assert response.status_code==200
    name=response.json()['name']
    import zipfile
    with zipfile.ZipFile(directory/'panel-backups'/name) as z:
        assert 'panel.db' in z.namelist() and 'WIEDERHERSTELLUNG.txt' in z.namelist()
    archive=directory/'servers'/sid/'Server/bad.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('auth.enc','blocked')
    assert c.post(f'/api/servers/{sid}/archive/extract',json={'path':'Server/bad.zip','destination':'Server/unpacked'}).status_code==403
    with zipfile.ZipFile(archive,'w') as z:z.writestr('hello.txt','valid')
    assert c.post(f'/api/servers/{sid}/archive/extract',json={'path':'Server/bad.zip','destination':'Server/unpacked'}).status_code==200
    assert (directory/'servers'/sid/'Server/unpacked/hello.txt').read_text()=='valid'
