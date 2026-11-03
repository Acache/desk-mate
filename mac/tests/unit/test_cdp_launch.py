from types import SimpleNamespace
from voice_assistant.adapters import cdp_player as cdp


def snapshot(ready=False,player=False,foreign=False):
    return dict(cdp_ready=ready,player_available=player,reachable=ready or foreign)


def test_encoded_chinese_app_home_is_recognized_without_a_player():
    from urllib.parse import quote
    assert cdp._bili_target({'url':quote('file:///Applications/哔哩哔哩.app/Contents/Resources/index.html',safe=':/')})
    assert not cdp._bili_target({'url':'https://example.com/index.html'})


def test_cold_launch_waits_for_cdp_without_requiring_a_video(monkeypatch):
    replies=iter([snapshot(),snapshot(True)])
    monkeypatch.setattr(cdp,'status',lambda:next(replies))
    monkeypatch.setattr(cdp,'app_running',lambda:False)
    monkeypatch.setattr(cdp,'_app_installed',lambda:True)
    calls=[]
    monkeypatch.setattr(cdp.subprocess,'run',lambda args,**kw:calls.append((args,kw)) or SimpleNamespace(returncode=0))
    result=cdp.enable_control()
    assert result['ok'] and result['code']=='BILI_CDP_READY'
    assert calls[0][0]==['open','-b',cdp.BUNDLE,'--args','--remote-debugging-port=9222']
    assert 'capture_output' not in calls[0][1]


def test_running_without_debug_requires_consent_and_does_not_mutate(monkeypatch):
    monkeypatch.setattr(cdp,'status',lambda:snapshot())
    monkeypatch.setattr(cdp,'app_running',lambda:True)
    monkeypatch.setattr(cdp,'_debug_flag_present',lambda:False)
    monkeypatch.setattr(cdp.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('no launch/kill before consent')))
    result=cdp.enable_control()
    assert result['requires_restart'] and result['code']=='BILI_RESTART_REQUIRED'


def test_ready_player_and_foreign_port_never_restart(monkeypatch):
    monkeypatch.setattr(cdp.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('unexpected subprocess')))
    monkeypatch.setattr(cdp,'status',lambda:snapshot(True,True))
    assert cdp.enable_control(True)['code']=='BILI_PLAYER_READY'
    monkeypatch.setattr(cdp,'status',lambda:snapshot(foreign=True))
    assert cdp.enable_control(True)['code']=='CDP_PORT_IN_USE'


def test_missing_app_and_launch_failure_have_clear_errors(monkeypatch):
    monkeypatch.setattr(cdp,'status',lambda:snapshot())
    monkeypatch.setattr(cdp,'app_running',lambda:False)
    monkeypatch.setattr(cdp,'_app_installed',lambda:False)
    assert cdp.enable_control()['code']=='BILI_NOT_INSTALLED'
    monkeypatch.setattr(cdp,'_app_installed',lambda:True)
    monkeypatch.setattr(cdp.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    assert cdp.enable_control()['code']=='BILI_LAUNCH_FAILED'


def test_confirmed_restart_targets_the_identified_main_process(monkeypatch):
    replies=iter([snapshot(),snapshot(True)])
    running=iter([True,False,False])
    monkeypatch.setattr(cdp,'status',lambda:next(replies))
    monkeypatch.setattr(cdp,'app_running',lambda:next(running))
    monkeypatch.setattr(cdp,'_application_info',lambda:{'pid':12345,'running':True,'installed':True})
    calls=[]
    monkeypatch.setattr(cdp.subprocess,'run',lambda args,**kw:calls.append(args) or SimpleNamespace(returncode=0))
    assert cdp.enable_control(confirmed_restart=True)['ok']
    assert calls[0]==['kill','-TERM','12345']
    assert calls[1][0]=='open'
