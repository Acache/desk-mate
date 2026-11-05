from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from voice_assistant.adapters.base import AdapterRegistry  # noqa: E402
from voice_assistant.adapters.fake import FakeBilibiliAdapter  # noqa: E402
from voice_assistant.api import create_app  # noqa: E402
from voice_assistant.config import load_config  # noqa: E402
from voice_assistant.decision import FakeDecision  # noqa: E402
from voice_assistant.stt import FakeSTT  # noqa: E402
from voice_assistant.storage import Database  # noqa: E402

R1 = (
    "bilibili.open", "bilibili.search", "bilibili.play", "bilibili.pause", "bilibili.resume",
)


def proposal(action, params=None):
    return {
        "schema_version": 1, "status": "ok", "reason_code": "none",
        "clarification": None, "model_confidence": None,
        "actions": [{"id": 0, "action": action, "params": params or {}, "after": []}],
    }


@pytest.fixture()
def service(tmp_path):
    cfg = load_config(tmp_path)
    db = Database(cfg.db_path)
    device_id, token = db.register_device("ESP32-C6 test")
    fake = FakeBilibiliAdapter()
    registry = AdapterRegistry()
    registry.register(fake, R1)
    stt = FakeSTT("打开B站")
    decision = FakeDecision(proposal("bilibili.open"))
    app = create_app(cfg, stt=stt, decision=decision, registry=registry)
    client = TestClient(app)
    with client:
        yield client, cfg, db, device_id, token, fake, stt, decision, app
    db.close()


def headers(device_id, token):
    return {"Authorization": f"Bearer {token}", "X-Device-ID": device_id}


def hello(ws, cfg, device_id, msg_id=1):
    ws.send_json({
        "v": 1, "type": "hello", "msg_id": msg_id, "device_id": device_id,
        "server_id": cfg.server.server_id, "firmware_version": "desk-mate-bili/1.0",
        "boot_id": str(uuid.uuid4()),
    })
    return ws.receive_json()


def frames(n=25, seq0=0):
    out = []
    pcm_all = b""
    for i in range(n):
        pcm = bytes([(seq0 + i) % 256, 1]) * 320
        out.append(struct.pack("<IHH", seq0 + i, 640, 0) + pcm)
        pcm_all += pcm
    return out, pcm_all


def test_health(service):
    client, cfg, db, device_id, token, fake, stt, decision, app = service
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["server_id"] == cfg.server.server_id
    assert "bilibili.open" in body["capabilities"]


def test_unauthenticated_ws_rejected(service):
    client, cfg, db, device_id, token, fake, stt, decision, app = service
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/remote", headers={"Authorization": "Bearer wrong", "X-Device-ID": device_id}) as ws:
            ws.receive_json()


def test_hello_stream_commit_result(service):
    client, cfg, db, device_id, token, fake, stt, decision, app = service
    with client.websocket_connect("/v1/remote", headers=headers(device_id, token)) as ws:
        ack = hello(ws, cfg, device_id)
        assert ack["type"] == "hello_ack"
        assert ack["ready"] is True
        command_id = str(uuid.uuid4())
        ws.send_json({
            "v": 1, "type": "begin", "msg_id": 2, "command_id": command_id,
            "kind": "audio", "format": {"encoding": "s16le", "rate": 16000, "channels": 1},
            "trigger": "esp32_ptt",
        })
        begin_ack = ws.receive_json()
        assert begin_ack["type"] == "begin_ack", begin_ack
        frames_data, pcm = frames()
        for frame in frames_data:
            ws.send_bytes(frame)
        # append acks may be interleaved; drain until no immediate frame by using
        # the final seal response as the synchronization point.
        digest = hashlib.sha256(pcm).hexdigest()
        ws.send_json({
            "v": 1, "type": "seal", "msg_id": 3, "command_id": command_id,
            "frame_count": len(frames_data), "pcm_bytes": len(pcm), "sha256": digest,
        })
        while True:
            msg = ws.receive_json()
            if msg["type"] == "seal_ack":
                break
        assert msg["type"] == "seal_ack", msg
        ws.send_json({"v": 1, "type": "commit", "msg_id": 4, "command_id": command_id, "sha256": digest})
        commit_ack = ws.receive_json()
        assert commit_ack["type"] == "commit_ack", commit_ack
        types = []
        while True:
            msg = ws.receive_json()
            types.append(msg["type"])
            if msg["type"] == "result":
                result = msg
                break
        assert result["state"] == "SUCCEEDED", result
        assert result["summary"]
        assert fake.state.invocations and fake.state.invocations[0][0] == "bilibili.open"
        ws.send_json({"v": 1, "type": "status", "msg_id": 5, "command_id": command_id})
        status = ws.receive_json()
        assert status["type"] == "status_snapshot"
        assert status["known"] is True and status["terminal"] is True
        assert status["result"]["state"] == "SUCCEEDED"


def test_cancel_before_commit_has_no_effect(service):
    client, cfg, db, device_id, token, fake, stt, decision, app = service
    with client.websocket_connect("/v1/remote", headers=headers(device_id, token)) as ws:
        hello(ws, cfg, device_id)
        command_id = str(uuid.uuid4())
        ws.send_json({
            "v": 1, "type": "begin", "msg_id": 2, "command_id": command_id,
            "kind": "audio", "format": {"encoding": "s16le", "rate": 16000, "channels": 1},
            "trigger": "esp32_ptt",
        })
        assert ws.receive_json()["type"] == "begin_ack"
        frames_data, _ = frames()
        for frame in frames_data:
            ws.send_bytes(frame)
        ws.send_json({"v": 1, "type": "cancel", "msg_id": 3, "command_id": command_id, "reason": "user"})
        while True:
            msg = ws.receive_json()
            if msg["type"] == "cancel_ack":
                break
        assert msg["outcome"] == "canceled"
        assert fake.state.invocations == []
        assert stt.calls == []


def test_wrong_server_id_requires_pairing(service):
    client, cfg, db, device_id, token, fake, stt, decision, app = service
    with client.websocket_connect("/v1/remote", headers=headers(device_id, token)) as ws:
        ws.send_json({
            "v": 1, "type": "hello", "msg_id": 1, "device_id": device_id,
            "server_id": str(uuid.uuid4()), "firmware_version": "x",
            "boot_id": str(uuid.uuid4()),
        })
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "PAIRING_REQUIRED"


def test_fragmented_json_rejected(service):
    client, cfg, db, device_id, token, fake, stt, decision, app = service
    with client.websocket_connect("/v1/remote", headers=headers(device_id, token)) as ws:
        hello(ws, cfg, device_id)
        # Sending a non-object JSON value is a protocol error; no state changes.
        ws.send_text("[]")
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "PROTOCOL_ERROR"


@pytest.mark.parametrize('text,model_used', [('播放第二个视频',False),('给我挑个适合现在看的',True)])
def test_home_context_stream_executes_without_any_search(service,text,model_used):
    client,cfg,db,device_id,token,fake,stt,decision,app=service
    items=[{'bvid':'BV1xx411c7mD','title':'第一段视频'}, {'bvid':'BV1yy411c7mD','title':'第二段视频'}]
    stt.text=text;fake.state.app_open=True
    ctx=app.state.voice.pipeline.ctx
    ctx.context_reader=lambda:{'page':'home','home_results':items}
    ctx.refresh_player=lambda:None
    decision.proposal=proposal('bilibili.play',{'bv':items[0]['bvid']})
    with client.websocket_connect('/v1/remote',headers=headers(device_id,token)) as ws:
        hello(ws,cfg,device_id)
        cid=str(uuid.uuid4())
        ws.send_json({'v':1,'type':'begin','msg_id':2,'command_id':cid,'kind':'audio',
            'format':{'encoding':'s16le','rate':16000,'channels':1},'trigger':'esp32_ptt'})
        assert ws.receive_json()['type']=='begin_ack'
        blocks,pcm=frames()
        for block in blocks:ws.send_bytes(block)
        digest=hashlib.sha256(pcm).hexdigest()
        ws.send_json({'v':1,'type':'seal','msg_id':3,'command_id':cid,'frame_count':len(blocks),'pcm_bytes':len(pcm),'sha256':digest})
        while ws.receive_json()['type']!='seal_ack':pass
        ws.send_json({'v':1,'type':'commit','msg_id':4,'command_id':cid,'sha256':digest})
        while True:
            result=ws.receive_json()
            if result['type']=='result':break
        assert result['state']=='SUCCEEDED',result
    assert fake.state.invocations[0][0]=='bilibili.play'
    assert bool(decision.calls)==model_used
    if model_used:assert decision.calls[0][1].page_context['page']=='home'
    else:assert fake.state.current_video==items[1]['bvid']
