import os
import socket
import threading
import io
import paramiko
import pytest
from hypanel import sftp, runtime, store


def test_real_sftp_authentication_isolation_and_running_write_guard(server,monkeypatch):
    c,directory,sid=server
    (directory/'servers'/sid/'Server/auth.enc').write_text('secret')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    monkeypatch.setenv('HYPANEL_SFTP_BIND','127.0.0.1');monkeypatch.setenv('HYPANEL_SFTP_PORT',str(port))
    stop,ready=threading.Event(),threading.Event()
    thread=threading.Thread(target=sftp.listen,args=(stop,ready));thread.start()
    assert ready.wait(5)
    transport=paramiko.Transport(('127.0.0.1',port))
    try:
        host_key=paramiko.RSAKey.from_private_key_file(str(directory/'sftp-host.key'))
        transport.connect(hostkey=host_key,username='admin+'+sid,password='test-password-strong')
        client=paramiko.SFTPClient.from_transport(transport)
        assert 'Server' in client.listdir('/')
        assert 'auth.enc' not in client.listdir('/Server')
        for path in ('/Server/auth.enc','/../panel.db','/Server/../../panel.db'):
            with pytest.raises(IOError):client.open(path).read()
        client.putfo(io.BytesIO(b'hello'),'/Server/qa.txt')
        assert client.open('/Server/qa.txt').read()==b'hello'
        assert runtime.EXTERNAL_WRITES.get(sid,0)==0
        monkeypatch.setattr(runtime,'active',lambda _:True)
        with pytest.raises(IOError):client.open('/Server/blocked.txt','w')
        monkeypatch.setattr(runtime,'active',lambda _:False)
        client.rename('/Server/qa.txt','/Server/renamed.txt');client.remove('/Server/renamed.txt')
        session=transport.open_session()
        with pytest.raises(paramiko.SSHException):session.exec_command('id')
        store.execute('UPDATE users SET disabled=1 WHERE username=?',('admin',))
        with pytest.raises(IOError):client.listdir('/')
        client.close()
    finally:
        transport.close();stop.set();thread.join(5)
    assert not thread.is_alive()
