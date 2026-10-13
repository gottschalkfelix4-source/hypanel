import io
import json
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from hypanel import files, runtime, store, operations


def test_file_editor_json_and_upload(server):
    c, directory, sid = server
    prefix=f'/api/servers/{sid}'
    assert c.put(prefix+'/files/content',json={"path":"Server/config.json","content":"{bad"}).status_code == 400
    assert c.put(prefix+'/files/content',json={"path":"Server/config.json","content":'{"ServerName":"Example"}'}).status_code == 200
    assert c.get(prefix+'/files/content',params={"path":"Server/config.json"}).json()['content'] == '{"ServerName":"Example"}'
    assert c.post(prefix+'/files/upload',data={"path":"Server/mods"},files={"file":('test.jar',b'jar')}).status_code == 200
    assert c.post(prefix+'/files/upload',data={"path":"Server/mods"},files={"file":('test.jar',b'jar')}).status_code == 409
    assert c.post(prefix+'/mods/toggle',json={"path":"test.jar"}).status_code == 200
    assert c.get(prefix+'/mods').json()[0]['enabled'] is False
    assert c.post(prefix+'/mods/toggle',json={"path":"../test.jar"}).status_code == 400


def test_file_moves_and_deletion(server):
    c, _, sid = server
    prefix=f'/api/servers/{sid}/files'
    assert c.post(prefix+'/mkdir',json={"path":"Server/example"}).status_code == 200
    c.put(prefix+'/content',json={"path":"Server/example/a.txt","content":"hello"})
    assert c.post(prefix+'/rename',json={"path":"Server/example/a.txt","destination":"Server/example/b.txt"}).status_code == 200
    assert c.get(prefix+'/download',params={"path":"Server/example/b.txt"}).content == b'hello'
    assert c.post(prefix+'/delete',json={"path":"Server/example"}).status_code == 200
    assert c.post(prefix+'/delete',json={"path":"Server"}).status_code == 400


def test_running_server_blocks_mutation(server, monkeypatch):
    c, _, sid = server
    monkeypatch.setattr(runtime,'active',lambda sid:True)
    prefix=f'/api/servers/{sid}'
    assert c.put(prefix+'/files/content',json={"path":"Server/a.txt","content":"x"}).status_code == 409
    assert c.put(prefix,json={"name":"changed"}).status_code == 409


def test_backup_excludes_credentials_and_optional_binaries(server):
    c, directory, sid = server
    base=directory/'servers'/sid
    (base/'Server/config.json').write_text('{"old":true}')
    (base/'Server/auth.enc').write_text('secret')
    (base/'Server/auth.key').write_text('secret')
    (base/'Server/HytaleServer.jar').write_bytes(b'jar')
    (base/'Assets.zip').write_bytes(b'assets')
    (base/'.hytale-downloader-credentials.json').write_text('secret')
    r=c.post(f'/api/servers/{sid}/backups');assert r.status_code==200,r.text
    zpath=directory/'backups'/sid/r.json()['name']
    with zipfile.ZipFile(zpath) as z:
        assert 'Server/config.json' in z.namelist()
        assert 'Server/auth.enc' not in z.namelist()
        assert 'Server/HytaleServer.jar' not in z.namelist()
        assert 'Assets.zip' not in z.namelist()
        assert not any('.hytale' in n for n in z.namelist())
    full=c.post(f'/api/servers/{sid}/backups?full=true').json()
    with zipfile.ZipFile(directory/'backups'/sid/full['name']) as z:
        assert 'Server/HytaleServer.jar' in z.namelist()
        assert 'Assets.zip' in z.namelist()


def test_restore_replaces_world_chunks_and_keeps_auth(server):
    c,directory,sid=server
    base=directory/'servers'/sid
    world=base/'Server/universe/worlds/default';world.mkdir()
    (world/'old.chunk').write_text('old')
    (base/'Server/auth.enc').write_text('secret')
    name=c.post(f'/api/servers/{sid}/backups').json()['name']
    (world/'new.chunk').write_text('new')
    (world/'old.chunk').write_text('changed')
    response=c.post(f'/api/servers/{sid}/backups/{name}/restore')
    assert response.status_code==200,response.text
    assert not (world/'new.chunk').exists()
    assert (world/'old.chunk').read_text()=='old'
    assert (base/'Server/auth.enc').read_text()=='secret'
    assert len(list((directory/'backups'/sid).glob('*.zip')))==2


def test_backup_stops_and_restarts_running_server(server,monkeypatch):
    c,_,sid=server
    game=MagicMock();game.status='running'
    monkeypatch.setattr(runtime,'container',lambda sid,install=False:None if install else game)
    stop, launch=MagicMock(),MagicMock()
    monkeypatch.setattr(runtime,'stop',stop);monkeypatch.setattr(runtime,'launch',launch)
    assert c.post(f'/api/servers/{sid}/backups').status_code==200
    stop.assert_called_once_with(sid)
    launch.assert_called_once()


def test_port_conflict_and_resource_validation(server):
    c,_,sid=server
    assert c.post('/api/servers',json={"name":"other","port":5520}).status_code==409
    assert c.post('/api/servers',json={"name":"other","port":5521,"memory_mb":512}).status_code==422
    assert c.post('/api/servers',json={"name":"other","port":5521,"cpus":0}).status_code==422


def test_clone_preserves_files_not_credentials(server):
    c,directory,sid=server
    base=directory/'servers'/sid
    (base/'Server/config.json').write_text('{}')
    (base/'Server/auth.enc').write_text('secret')
    (base/'.hytale-downloader-credentials.json').write_text('secret')
    r=c.post(f'/api/servers/{sid}/clone',json={"name":"cloned","port":5521})
    assert r.status_code==200,r.text
    destination=directory/'servers'/r.json()['id']
    assert (destination/'Server/config.json').read_text()=='{}'
    assert not (destination/'Server/auth.enc').exists()
    assert not (destination/'.hytale-downloader-credentials.json').exists()
    assert (directory/'identities'/sid).read_text()!=(directory/'identities'/r.json()['id']).read_text()


def test_import_only_server_payload_preserves_config(server):
    c,directory,sid=server
    (directory/'servers'/sid/'Server/config.json').write_text('{"preserved":true}')
    body=io.BytesIO()
    with zipfile.ZipFile(body,'w') as z:
        z.writestr('package/Server/HytaleServer.jar',b'jar')
        z.writestr('package/Assets.zip',b'assets')
        z.writestr('package/Server/config.json','{"overwrite":true}')
        z.writestr('package/Server/auth.enc','malicious')
    r=c.post(f'/api/servers/{sid}/import',files={"file":('game.zip',body.getvalue())})
    assert r.status_code==200,r.text
    base=directory/'servers'/sid
    assert (base/'Server/config.json').read_text()=='{"preserved":true}'
    assert not (base/'Server/auth.enc').exists()
    assert (base/'Assets.zip').read_bytes()==b'assets'


def test_schedules_validation_and_timezone(server):
    c,_,sid=server
    prefix=f'/api/servers/{sid}/schedules'
    assert c.post(prefix,json={"name":"bad","cron":"nonsense","action":"backup"}).status_code==400
    assert c.post(prefix,json={"name":"bad","cron":"* * * * *","timezone":"Invalid","action":"backup"}).status_code==400
    assert c.post(prefix,json={"name":"bad","cron":"* * * * *","action":"command","command":"stop\nstart"}).status_code==400
    r=c.post(prefix,json={"name":"backup","cron":"0 4 * * *","timezone":"Europe/Berlin","action":"backup"})
    assert r.status_code==201,r.text
    assert r.json()['next_run']>time.time()
    assert c.delete(prefix+'/'+r.json()['id']).status_code==200
    assert c.get(prefix).json()==[]


def test_delete_requires_name_and_preserves_backup(server):
    c,directory,sid=server
    assert c.delete(f'/api/servers/{sid}?confirmation=wrong').status_code==400
    r=c.delete(f'/api/servers/{sid}',params={"confirmation":"Test server"})
    assert r.status_code==200,r.text
    assert not (directory/'servers'/sid).exists()
    assert len(list((directory/'backups'/sid).glob('*.zip')))==1


def test_archive_decompression_limit(tmp_path,monkeypatch):
    path=tmp_path/'too-large.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('a',b'12345')
    monkeypatch.setattr(files,'MAX_ARCHIVE_BYTES',4)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        files.extract_zip(path,tmp_path/'extract')


def test_console_commands_never_use_shell(monkeypatch):
    c=MagicMock();c.status='running';c.exec_run.return_value=MagicMock(exit_code=0,output=b'OK\n')
    monkeypatch.setattr(runtime,'container',lambda sid:c)
    runtime.command('id','say $(touch /evil)')
    args=c.exec_run.call_args.args[0]
    assert args[0]=='python3' and args[-1]=='say $(touch /evil)'
    assert 'sh' not in args
    from fastapi import HTTPException
    with pytest.raises(HTTPException):runtime.command('id','help\nstop')
