from voice_assistant.adapters.bilibili import BilibiliAdapter
from voice_assistant.adapters import cdp_player


def test_home_foreground_does_not_reuse_an_old_search_window(monkeypatch):
    pages={'index.html':{'page':'home','home_results':[{'bvid':'BV1xx411c7mD'}]},
           'search.html':{'page':'search','search_results':[{'bvid':'BV1yy411c7mD'}]},
           'player.html':{'page':'player'}}
    class Session:
        def __init__(self,marker):self.marker=marker
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read_context(self):return pages[self.marker]
    monkeypatch.setattr(cdp_player,'control_available',lambda:True)
    monkeypatch.setattr(cdp_player,'ws_for',lambda marker:marker)
    monkeypatch.setattr(cdp_player,'CdpPlayer',Session)
    adapter=BilibiliAdapter.__new__(BilibiliAdapter)
    adapter._helper=lambda *args,**kw:{'windows':[{'name':'哔哩哔哩'}]}
    assert adapter.context_snapshot()['page']=='home'
    pages['player.html']['focused']=True
    assert adapter.context_snapshot()['page']=='player'


def test_context_can_be_read_with_only_a_home_target(monkeypatch):
    class Session:
        def __init__(self, ws): assert ws == 'home-ws'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read_context(self): return {'page':'home','home_results':[{'bvid':'BV1jVJk6AEmn'}]}
    monkeypatch.setattr(cdp_player,'_targets',lambda:[{
        'url':'https://bilipc.bilibili.com/index.html#/page/home/recommends',
        'webSocketDebuggerUrl':'home-ws'}])
    monkeypatch.setattr(cdp_player,'CdpPlayer',Session)
    adapter=BilibiliAdapter.__new__(BilibiliAdapter)
    adapter._helper=lambda *args,**kw:{'windows':[{'name':'哔哩哔哩'}]}
    assert not cdp_player.available()
    assert adapter.context_snapshot()['home_results'][0]['bvid']=='BV1jVJk6AEmn'
