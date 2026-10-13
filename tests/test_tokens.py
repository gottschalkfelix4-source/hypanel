from hypanel import store


def test_api_key_hash_authorization_and_revocation(authenticated):
    c,_=authenticated
    created=c.post('/api/me/tokens',json={"name":"Automation","days":1})
    assert created.status_code==201,created.text
    value=created.json()['token'];tid=created.json()['id']
    assert store.query('SELECT token FROM tokens WHERE id=?',(tid,),one=True)['token']!=value
    assert 'token' not in c.get('/api/me/tokens').json()[0]
    c.cookies.clear();c.headers.pop('X-CSRF-Token')
    c.headers['Authorization']='Bearer '+value
    assert c.get('/api/me').status_code==200
    assert c.post('/api/servers',json={"name":"API server","port":5530}).status_code==201
    assert c.post('/api/me/tokens',json={"name":"chained"}).status_code==403
    assert c.delete('/api/me/tokens/'+tid).status_code==200
    assert c.get('/api/me').status_code==401


def test_api_key_expiry_and_disabled_user(authenticated):
    c,_=authenticated
    token=c.post('/api/me/tokens',json={"name":"Expired"}).json()
    store.execute('UPDATE tokens SET expires=0 WHERE id=?',(token['id'],))
    assert c.get('/api/me',headers={"Authorization":"Bearer "+token['token']}).status_code==401


def test_request_body_limit(authenticated):
    c,_=authenticated
    assert c.post('/api/servers',content=b'x',headers={"Content-Length":str(5*1024**2)}).status_code==413
