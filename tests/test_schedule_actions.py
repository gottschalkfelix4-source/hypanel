import time
from unittest.mock import MagicMock
from hypanel import operations, runtime, store


def test_schedule_pause_edit_and_manual_execution(server,monkeypatch):
    c,_,sid=server
    prefix=f'/api/servers/{sid}/schedules'
    job=c.post(prefix,json={"name":"original","cron":"* * * * *","action":"command","command":"help"}).json()
    result=c.put(prefix+'/'+job['id'],json={"name":"changed","cron":"0 4 * * *","action":"command","command":"auth status","enabled":False})
    assert result.status_code==200,result.text
    updated=c.get(prefix).json()[0]
    assert updated['enabled']==0 and updated['command']=='auth status'
    command=MagicMock();monkeypatch.setattr(runtime,'command',command)
    assert c.post(prefix+'/'+job['id']+'/run').status_code==200
    command.assert_called_once_with(sid,'auth status')
    assert c.get(prefix).json()[0]['last_result']=='OK'


def test_scheduler_claims_overdue_job_once(server,monkeypatch):
    c,_,sid=server
    prefix=f'/api/servers/{sid}/schedules'
    job=c.post(prefix,json={"name":"overdue","cron":"* * * * *","action":"backup"}).json()
    store.execute('UPDATE schedules SET next_run=0 WHERE id=?',(job['id'],))
    action=MagicMock();monkeypatch.setattr(operations,'scheduled_action',action)
    class Ticks:
        count=0
        def wait(self,seconds):
            self.count+=1
            return self.count>2
    operations.scheduler(Ticks())
    assert action.call_count==1
    result=store.query('SELECT * FROM schedules WHERE id=?',(job['id'],),one=True)
    assert result['next_run']>time.time() and result['last_result']=='OK'


def test_api_docs_are_self_contained(authenticated):
    c,_=authenticated
    r=c.get('/docs')
    assert r.status_code==200
    assert 'api.js' in r.text
    assert 'cdn' not in r.text
    assert '/api/servers' in c.get('/openapi.json').json()['paths']
