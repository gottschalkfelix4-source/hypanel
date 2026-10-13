import io
import os
import stat
import zipfile
import pytest
from fastapi import HTTPException
from hypanel import files, store


def test_setup_requires_secret_and_only_once(panel):
    c, directory = panel
    payload = {"username": "admin", "password": "test-password-strong", "setup_key": "wrong"}
    assert c.post("/api/setup", json=payload).status_code == 403
    payload["setup_key"] = (directory / "setup.key").read_text()
    assert c.post("/api/setup", json=payload).status_code == 200
    assert not (directory / "setup.key").exists()
    assert c.post("/api/setup", json=payload).status_code == 409


def test_authentication_csrf_origin_and_headers(authenticated):
    c, _ = authenticated
    assert c.post("/api/servers", headers={"X-CSRF-Token": "bad"}, json={"name": "X"}).status_code == 403
    assert c.post("/api/servers", headers={"Origin": "https://evil.example"}, json={"name": "X"}).status_code == 403
    response = c.get("/api/me")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/me").status_code == 401


def test_session_cookie_and_storage(authenticated):
    c, _ = authenticated
    cookie = c.cookies.get("hypanel_session")
    stored = store.query("SELECT token FROM sessions", one=True)["token"]
    assert stored != cookie and len(stored) == 64
    r = c.post("/api/login", json={"username": "admin", "password": "test-password-strong"})
    assert "HttpOnly" in r.headers["set-cookie"]
    assert "SameSite=strict" in r.headers["set-cookie"]


def test_last_administrator_guard(authenticated):
    c, _ = authenticated
    uid = c.get("/api/me").json()["id"]
    assert c.put('/api/users/'+uid, json={"role": "member"}).status_code == 409
    assert c.put('/api/users/'+uid, json={"role": "admin", "disabled": True}).status_code == 409


def test_password_change_revokes_sessions(authenticated):
    c, _ = authenticated
    assert c.post('/api/me/password', json={"current": "wrong", "password": "new-test-password"}).status_code == 403
    assert c.post('/api/me/password', json={"current": "test-password-strong", "password": "new-test-password"}).status_code == 200
    assert c.get('/api/me').status_code == 401
    assert c.post('/api/login', json={"username":"admin","password":"new-test-password"}).status_code == 200


def test_rbac_server_assignment_and_read_only(server):
    c, _, sid = server
    uid = c.post('/api/users', json={"username":"reader","password":"test-password-reader"}).json()['id']
    assert c.put(f'/api/servers/{sid}/access', json={"user_id":uid,"permission":"viewer"}).status_code == 200
    login = c.post('/api/login', json={"username":"reader","password":"test-password-reader"}).json()
    c.headers['X-CSRF-Token'] = login['csrf']
    assert len(c.get('/api/servers').json()) == 1
    assert c.get(f'/api/servers/{sid}/files').status_code == 200
    assert c.get('/api/users').status_code == 403
    assert c.post(f'/api/servers/{sid}/power/start').status_code == 403
    assert c.put(f'/api/servers/{sid}/files/content', json={"path":"Server/a.json","content":"{}"}).status_code == 403
    assert c.get('/api/servers/unknown').status_code == 404


def test_members_cannot_enumerate_other_servers(server):
    c, _, sid = server
    c.post('/api/users', json={"username":"member","password":"test-password-member"})
    login = c.post('/api/login', json={"username":"member","password":"test-password-member"}).json()
    c.headers['X-CSRF-Token'] = login['csrf']
    assert c.get('/api/servers').json() == []
    assert c.get(f'/api/servers/{sid}').status_code == 403


@pytest.mark.parametrize('path', ['../panel.db', '/etc/passwd', 'Server/../../panel.db', 'C:/Windows/test', 'Server\\secret', 'Server/auth.enc', 'Server/auth.key', '.machine-id'])
def test_path_escape_and_credentials_blocked(server, path):
    c, _, sid = server
    assert c.get(f'/api/servers/{sid}/files/content', params={"path":path}).status_code in {400,403}


def test_symlink_escape(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    target = tmp_path / 'outside'; target.mkdir()
    try:
        (base / 'link').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('OS does not allow symlink creation')
    with pytest.raises(HTTPException):
        files.safe_path(base, 'link/secret.txt')


@pytest.mark.parametrize('filename', ['../escape.txt', '/absolute.txt', 'Server/../../escape.txt', 'C:/bad', 'a\\b'])
def test_zip_slip(tmp_path, filename):
    zpath = tmp_path / 'bad.zip'
    with zipfile.ZipFile(zpath, 'w') as z:
        info = zipfile.ZipInfo('placeholder')
        info.filename = filename
        z.writestr(info, 'evil')
    destination = tmp_path / 'unpacked'; destination.mkdir()
    with pytest.raises(HTTPException):
        files.extract_zip(zpath, destination)
    assert list(destination.rglob('*')) == []


def test_archive_symlink(tmp_path):
    zpath=tmp_path/'link.zip'
    info=zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK | 0o777)<<16
    with zipfile.ZipFile(zpath,'w') as z:
        z.writestr(info,'/etc/passwd')
    with pytest.raises(HTTPException):
        files.extract_zip(zpath,tmp_path/'extract')


def test_login_rate_limit(panel):
    c, _ = panel
    for _ in range(10):
        assert c.post('/api/login',json={"username":"wrong","password":"bad"}).status_code == 401
    assert c.post('/api/login',json={"username":"wrong","password":"bad"}).status_code == 429
