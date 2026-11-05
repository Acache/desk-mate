"""Release-path regressions: staging must never become a privacy client."""
from pathlib import Path
import importlib.util
import json
import plistlib
import shutil
import subprocess
import sys

import pytest


def load_updater(monkeypatch):
    path=Path(__file__).resolve().parents[2]/'scripts/update_local_app.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location('local_updater_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture_repo(tmp_path):
    app=tmp_path/'build/usability-release/Desk-Mate.app'
    (app/'Contents/MacOS').mkdir(parents=True)
    (app/'Contents/MacOS/DeskMate').write_text('old executable')
    resources=app/'Contents/Resources/mac'
    for name in ('src','prompts','bin'):(resources/name).mkdir(parents=True)
    (resources/'src/old.py').write_text('old runtime')
    (resources.parent/'bundle.json').write_text(json.dumps({'signed_identity':'-'}))
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleVersion':'6','CFBundleName':'.Desk-Mate-staging.app'}))
    for name in ('src','prompts','app','scripts'):(tmp_path/'mac'/name).mkdir(parents=True)
    (tmp_path/'mac/src/new.py').write_text('new runtime')
    (tmp_path/'mac/app/version.json').write_text(json.dumps({'version':'0.3.1','build':7}))
    return app


def run_update(tmp_path,monkeypatch,fail_check=False):
    mod=load_updater(monkeypatch);app=fixture_repo(tmp_path)
    monkeypatch.setattr(mod,'REPO',tmp_path);monkeypatch.setattr(mod,'APP',app)
    monkeypatch.setattr('sys.argv',['update_local_app.py'])
    monkeypatch.setattr(mod.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,1))
    monkeypatch.setattr(mod,'ensure_local_identity',lambda:{'identity':'A'*40,'keychain':'/private/keychain'})
    monkeypatch.setattr(mod,'verify_local_signature',lambda *a:'normal certificate requirement')
    calls=[]
    def run(*args,**kw):
        args=tuple(str(x) for x in args);calls.append(args)
        if args[0]=='cp':shutil.copytree(Path(args[-2]),Path(args[-1]))
        if args[0]=='swiftc':
            output=Path(args[args.index('-o')+1]);output.parent.mkdir(parents=True,exist_ok=True);output.write_text('new executable')
        if '--self-check' in args:
            assert args[0]==str(app/'Contents/MacOS/DeskMate')
            assert not (app/'Contents/Resources/mac/src/old.py').exists()
            if fail_check:raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(mod,'run',run)
    return mod,app,calls


def test_staging_is_never_executed_and_final_app_has_product_name(tmp_path,monkeypatch):
    mod,app,calls=run_update(tmp_path,monkeypatch)
    mod.main()
    executions=[call for call in calls if call[0].endswith('/Contents/MacOS/DeskMate')]
    assert executions and all('.Desk-Mate-staging.app' not in call[0] for call in executions)
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    assert info['CFBundleName']==info['CFBundleDisplayName']=='Desk-Mate'
    assert info['CFBundleVersion']=='7'
    assert (app/'Contents/Resources/mac/src/new.py').is_file()
    registrations=[call for call in calls if call[0].endswith('/lsregister')]
    if registrations:
        assert registrations[-1][1:]==('-f',str(app))
        assert {call[-1] for call in registrations if call[1]=='-u'} == {
            str(app.parent/'.Desk-Mate-staging.app'),str(tmp_path/'build/2026-11-05/Desk-Mate.app')}
    assert not (app.parent/'.Desk-Mate-previous.app').exists()
    signing=[call for call in calls if call[0]=='codesign' and '--sign' in call]
    assert signing and all(call[call.index('--sign')+1]=='A'*40 for call in signing)
    assert all(call[call.index('--keychain')+1]=='/private/keychain' for call in signing)
    metadata=json.loads((app/'Contents/Resources/bundle.json').read_text())
    assert metadata['signed_identity']=='A'*40 and metadata['signing_kind']=='local-certificate'
    assert calls[-1]==('/usr/bin/security','lock-keychain','/private/keychain')


def test_failed_canonical_check_restores_previous_app(tmp_path,monkeypatch):
    mod,app,calls=run_update(tmp_path,monkeypatch,fail_check=True)
    with pytest.raises(subprocess.CalledProcessError):mod.main()
    assert (app/'Contents/MacOS/DeskMate').read_text()=='old executable'
    assert (app/'Contents/Resources/mac/src/old.py').is_file()
    assert not (app.parent/'.Desk-Mate-staging.app').exists()


def test_explicit_signing_identity_does_not_create_local_keys(tmp_path,monkeypatch):
    mod,app,calls=run_update(tmp_path,monkeypatch)
    monkeypatch.setattr('sys.argv',['update_local_app.py','--identity','specific-certificate'])
    monkeypatch.setattr(mod,'ensure_local_identity',lambda:pytest.fail('explicit identity must be respected'))
    mod.main()
    signing=[call for call in calls if call[0]=='codesign' and '--sign' in call]
    assert signing and all(call[call.index('--sign')+1]=='specific-certificate' for call in signing)
    assert all('--keychain' not in call for call in signing)


def test_wrong_local_signature_never_stops_or_replaces_running_app(tmp_path,monkeypatch):
    mod,app,calls=run_update(tmp_path,monkeypatch)
    def reject(*args):raise RuntimeError('signature identity mismatch')
    monkeypatch.setattr(mod,'verify_local_signature',reject)
    with pytest.raises(RuntimeError,match='signature identity mismatch'):mod.main()
    assert (app/'Contents/MacOS/DeskMate').read_text()=='old executable'
    assert not any(call[0]=='swift' for call in calls)
    assert calls[-1]==('/usr/bin/security','lock-keychain','/private/keychain')
