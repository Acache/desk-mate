import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from voice_assistant.desktop_control import BleController, control_path, request_selection, start_control_server


def state_for(root):
    return SimpleNamespace(cfg=SimpleNamespace(root=root),db=SimpleNamespace(active_command=lambda:None),
                           ble_status={},stt=object(),decision=object(),ble_switching=False)


def test_switch_stops_old_connector_and_keeps_models(tmp_path):
    async def run():
        state=state_for(tmp_path); events=[]; stt,model=state.stt,state.decision
        async def connect(state,address):
            events.append(("start",address))
            try:
                await asyncio.Future()
            finally:
                events.append(("stop",address))
        control=BleController(state,connect)
        a,b=str(uuid4()).upper(),str(uuid4()).upper()
        assert (await control.select(a,"第一块板"))["ok"]
        await asyncio.sleep(0)
        assert (await control.select(b,"第二块板"))["ok"]
        await asyncio.sleep(0)
        assert events[:3]==[("start",a),("stop",a),("start",b)]
        assert state.stt is stt and state.decision is model
        assert state.ble_status["address"]==b and not state.ble_status["authenticated"]
        assert json.loads((tmp_path/"desktop.json").read_text())["ble_address"]==b
        assert (await control.select(None))["code"]=="BLE_DISCONNECTED"
        assert events[-1]==("stop",b)
        await control.close()
    asyncio.run(run())


def test_busy_selection_does_not_disconnect_or_change_settings(tmp_path):
    async def run():
        state=state_for(tmp_path)
        state.db.active_command=lambda:{"state":"TRANSCRIBING"}
        control=BleController(state,lambda *a:None)
        result=await control.select(str(uuid4()))
        assert result["code"]=="COMMAND_BUSY"
        assert not (tmp_path/"desktop.json").exists()
        assert control.task is None and not state.ble_switching
    asyncio.run(run())


def test_disconnect_timeout_does_not_start_a_second_input(tmp_path,monkeypatch):
    async def run():
        state=state_for(tmp_path); started=[]
        async def connect(state,address):
            started.append(address)
            await asyncio.Future()
        control=BleController(state,connect)
        await control.select(str(uuid4()))
        await asyncio.sleep(0)
        async def stuck(tasks,timeout):
            return set(),tasks
        monkeypatch.setattr(asyncio,'wait',stuck)
        result=await control.select(str(uuid4()))
        assert result['code']=='BLE_DISCONNECT_TIMEOUT'
        assert len(started)==1 and state.ble_switching
        await control.close()
    asyncio.run(run())


def test_private_control_socket_and_disconnect(tmp_path):
    async def run():
        state=state_for(tmp_path)
        async def connect(state,address):
            await asyncio.Future()
        state.ble_controller=BleController(state,connect)
        server,path=await start_control_server(state)
        try:
            assert path.stat().st_mode & 0o777 == 0o600
            result=await asyncio.to_thread(request_selection,tmp_path,str(uuid4()),"新设备")
            assert result["ok"] and result["code"]=="BLE_CONNECTING"
            result=await asyncio.to_thread(request_selection,tmp_path,None)
            assert result["code"]=="BLE_DISCONNECTED"
        finally:
            server.close(); await server.wait_closed(); path.unlink(missing_ok=True)
            await state.ble_controller.close()
    asyncio.run(run())


def test_control_path_is_short_and_invalid_target_is_rejected(tmp_path):
    assert len(str(control_path(tmp_path/('a'*150))).encode())<104
    result=request_selection(tmp_path,'not a device address')
    assert result["code"]=='INVALID_DEVICE'


def test_offline_selection_is_saved_and_running_startup_is_not_overwritten(tmp_path,monkeypatch):
    import fcntl
    from voice_assistant.app_runtime import select_device
    import voice_assistant.desktop_control as desktop
    monkeypatch.setattr(desktop,'request_selection',lambda *a:desktop.failure('SERVICE_UNAVAILABLE','test'))
    address=str(uuid4()).upper()
    assert select_device(tmp_path,address,'我的设备')["code"]=='BLE_SAVED'
    with (tmp_path/'desktop.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=select_device(tmp_path,str(uuid4()))
        assert result['code']=='SERVICE_STARTING'
    assert json.loads((tmp_path/'desktop.json').read_text())['ble_address']==address


def test_ble_errors_are_actionable():
    from voice_assistant.ble_bridge import connection_error
    assert connection_error(RuntimeError('USB authorization required or revoked'),'authenticating')['error_code']=='BLE_AUTH_REQUIRED'
    assert connection_error(RuntimeError('Connect this Mac on Desk-Mate page 2 first'),'resolving')['error_code']=='BLE_SELECT_MAC'
    assert connection_error(RuntimeError('not authorized'),'scanning')['error_code']=='BLE_PERMISSION'
    assert connection_error(TimeoutError(),'connecting')['hint']
