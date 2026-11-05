import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_assistant.app_runtime import bundle_environment


def test_bundle_paths_ignore_developer_environment(tmp_path,monkeypatch):
    resources = tmp_path/"My App.app/Contents/Resources"
    resources.mkdir(parents=True)
    (resources/"bundle.json").write_text(json.dumps({"model":"qwen2.5:1.5b"}))
    monkeypatch.setenv("PYTHONHOME","/developer/python")
    monkeypatch.setenv("DYLD_LIBRARY_PATH","/opt/homebrew/lib")
    monkeypatch.setenv("OLLAMA_HOST","0.0.0.0:11434")
    env = bundle_environment(resources,tmp_path/"data",11435)
    assert "PYTHONHOME" not in env and "DYLD_LIBRARY_PATH" not in env
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["VOICE_STT_MODEL"] == str(resources/"models/sensevoice")
    assert env["HF_HUB_OFFLINE"] == "1"


def test_builder_copies_only_manifest_referenced_blobs(tmp_path):
    spec = importlib.util.spec_from_file_location("builder",Path(__file__).parents[2]/"scripts/build_app.py")
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    source = tmp_path/"source"
    manifest = source/"manifests/registry.ollama.ai/library/model/tag"
    manifest.parent.mkdir(parents=True)
    blobs = source/"blobs"; blobs.mkdir()
    content = b"test model"
    import hashlib
    digest = hashlib.sha256(content).hexdigest()
    (blobs/f"sha256-{digest}").write_bytes(content)
    (blobs/"unrelated-private-model").write_bytes(b"do not ship")
    manifest.write_text(json.dumps({"config":{"digest":f"sha256:{digest}"},"layers":[]}))
    result = builder.copy_ollama_model(source,tmp_path/"bundle","model:tag")
    assert result["blobs"] == [f"sha256:{digest}"]
    assert not (tmp_path/"bundle/blobs/unrelated-private-model").exists()


def test_diagnostic_export_excludes_private_data(tmp_path):
    import zipfile
    from voice_assistant.app_runtime import export_diagnostics
    root = tmp_path/"data"; root.mkdir()
    (root/"logs").mkdir()
    (root/"logs/service.log").write_text("x"*300000)
    (root/"learning.db").write_text("private database")
    (root/"private").mkdir()
    (root/"private/ca.key").write_text("private key")
    resource = tmp_path/"resources";resource.mkdir()
    (resource/"bundle.json").write_text('{"model":"test"}')
    output = tmp_path/"diagnostics.zip"
    export_diagnostics(root,resource,output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"bundle.json","service.log"}
        assert len(archive.read("service.log")) == 256*1024


@pytest.mark.parametrize('force,unchanged',[(False,True),(False,False),(True,False)])
def test_pair_command_distinguishes_saved_record_from_new_usb_ack(tmp_path,monkeypatch,capsys,force,unchanged):
    from voice_assistant import app_runtime,provisioning,storage
    monkeypatch.setenv('VOICE_ASSISTANT_DATA_DIR',str(tmp_path/'original'))
    calls=[];closed=[]
    cfg=SimpleNamespace(db_path=tmp_path/'test.db',port=0)
    monkeypatch.setattr(app_runtime,'load_config',lambda:cfg)
    monkeypatch.setattr(storage,'Database',lambda path:SimpleNamespace(close=lambda:closed.append(True)))
    def pair(*args,**kwargs):
        calls.append((args,kwargs));return {'unchanged':unchanged,'authorization_verified':False,'device_id':'registered-device'}
    monkeypatch.setattr(provisioning,'pair_device',pair)
    argv=['--resources',str(tmp_path/'resources'),'--root',str(tmp_path),'pair','--serial-port','/dev/cu.test']
    if force:argv+=['--force']
    assert app_runtime.main(argv)==0
    result=json.loads(capsys.readouterr().out)
    assert calls[0][1]=={'force':force} and closed==[True]
    assert result['code']==('USB_ALREADY_REGISTERED' if unchanged else 'USB_PAIRED')
    assert result['data']['authorization_verified'] is False
    if unchanged:
        assert result['message']=='已有本机授权记录'
        assert '重新连接' in result['hint'] and '高级' in result['hint']
        assert '已连接' not in result['message'] and '已验证' not in result['message']


def test_runtime_reconnect_uses_existing_private_service(tmp_path,monkeypatch,capsys):
    from voice_assistant import app_runtime,desktop_control
    monkeypatch.setenv('VOICE_ASSISTANT_DATA_DIR',str(tmp_path/'original'))
    calls=[]
    def request(root):
        calls.append(root);return {'ok':True,'code':'BLE_RECONNECTING','address':'existing'}
    monkeypatch.setattr(desktop_control,'request_reconnect',request)
    assert app_runtime.main(['--resources',str(tmp_path/'resources'),'--root',str(tmp_path),'reconnect'])==0
    assert calls==[tmp_path] and json.loads(capsys.readouterr().out)['code']=='BLE_RECONNECTING'
    assert not (tmp_path/'desktop.json').exists()


def test_cli_pair_force_is_explicit_and_history_does_not_claim_new_pair(tmp_path,monkeypatch,capsys):
    from voice_assistant import cli,provisioning
    closed=[];calls=[]
    monkeypatch.setattr(cli,'_ctx',lambda args:(SimpleNamespace(),SimpleNamespace(close=lambda:closed.append(True))))
    monkeypatch.setattr(provisioning,'choose_port',lambda port:port)
    def pair(*args,**kwargs):
        calls.append(kwargs);return {'unchanged':True,'authorization_verified':False,'device_id':'known'}
    monkeypatch.setattr(provisioning,'pair_device',pair)
    assert not cli.build_parser().parse_args(['pair']).force
    assert cli.main(['pair','--port','/dev/cu.test','--force'])==0
    result=json.loads(capsys.readouterr().out)
    assert calls==[{'force':True}] and closed==[True]
    assert result['paired'] is False and result['unchanged'] is True
    assert result['code']=='USB_ALREADY_REGISTERED'


@pytest.mark.parametrize('code,expected',[('COMMAND_BUSY','语音'),('USB_AUTH_HISTORY_INVALID','记录'),('USB_AUTH_SAVE_FAILED','设备已接收新凭据')])
def test_usb_operation_errors_keep_actual_cause(code,expected):
    from voice_assistant.app_runtime import operation_error
    from voice_assistant.provisioning import ProvisioningError
    result=operation_error('pair',ProvisioningError('diagnostic only',code))
    assert result['ok'] is False and result['code']==code
    assert expected in result['message']
    assert 'USB 数据线' not in result['hint']
    if code == 'USB_AUTH_SAVE_FAILED':
        assert '存储' in result['hint'] and '高级' in result['hint']


def test_successful_usb_ack_warning_is_detail_not_another_authorization_failure():
    from voice_assistant.app_runtime import usb_pair_result
    warning='授权已保存，但诊断摘要未能保存；无需重复授权'
    result=usb_pair_result({'unchanged':False,'warning':warning})
    assert result['ok'] is True and result['code']=='USB_PAIRED'
    assert result['detail']==warning
    assert '查看连接状态' in result['hint'] and '只有首次连接' in result['hint']
    assert '重新连接' in result['hint'] and '无需再次授权' in result['hint']
