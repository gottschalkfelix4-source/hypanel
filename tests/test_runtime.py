from unittest.mock import MagicMock
from hypanel import runtime, store


def test_game_container_isolation_and_native_library_support(server,monkeypatch):
    _,_,sid=server
    record=store.query('SELECT * FROM servers WHERE id=?',(sid,),one=True)
    docker=MagicMock()
    monkeypatch.setattr(runtime,'client',lambda:docker)
    runtime.launch(record)
    kwargs=docker.containers.run.call_args.kwargs
    assert kwargs['user']=='99:100'
    assert kwargs['cap_drop']==['ALL']
    assert kwargs['security_opt']==['no-new-privileges:true']
    assert kwargs['read_only'] is True
    assert 'noexec' not in kwargs['tmpfs']['/tmp']
    assert kwargs['pids_limit']==512
    assert kwargs['ports']=={'5520/udp':5520}
    assert kwargs['mem_limit']=='4096m'
    assert kwargs['nano_cpus']==2_000_000_000
    assert not any('docker.sock' in mount for mount in kwargs['volumes'])
    assert any('/identities/'+sid in mount for mount in kwargs['volumes'])


def test_installer_never_publishes_udp_ports(server,monkeypatch):
    _,_,sid=server
    record=store.query('SELECT * FROM servers WHERE id=?',(sid,),one=True)
    docker=MagicMock();monkeypatch.setattr(runtime,'client',lambda:docker)
    runtime.launch(record,install=True)
    kwargs=docker.containers.run.call_args.kwargs
    assert kwargs['ports']=={}
    assert kwargs['environment']['HYPANEL_MODE']=='install'
    assert kwargs['restart_policy']=={'Name':'no'}
