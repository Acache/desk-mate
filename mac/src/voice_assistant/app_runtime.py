"""Private process supervisor and native app commands for the macOS bundle."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .config import load_config, write_private

DEFAULT_ROOT = Path.home()/"Library/Application Support/desk-mate"


def bundle_environment(resources: Path, root: Path, ollama_port: int) -> dict[str, str]:
    metadata = json.loads((resources/"bundle.json").read_text())
    env = {k:v for k,v in os.environ.items() if not k.startswith(("PYTHON", "DYLD_", "OLLAMA_"))}
    env.update(PYTHONPATH=str(resources/"mac/src"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
               VOICE_ASSISTANT_DATA_DIR=str(root), VOICE_STT_MODEL=str(resources/"models/sensevoice"),
               VOICE_ASSISTANT_DESKTOP="1",
               VOICE_ASSISTANT_OLLAMA_MODEL=metadata["model"],
               VOICE_STT_MODEL_DIGEST=metadata.get("stt_model_sha256","bundled-sensevoice"),
               VOICE_ASSISTANT_OLLAMA_URL=f"http://127.0.0.1:{ollama_port}",
               OLLAMA_HOST=f"127.0.0.1:{ollama_port}", OLLAMA_MODELS=str(resources/"models/ollama"),
               OLLAMA_NO_CLOUD="1", OLLAMA_KEEP_ALIVE="30m", HF_HUB_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", MODELSCOPE_CACHE=str(root/"cache/modelscope"),
               HF_HOME=str(root/"cache/huggingface"), MPLCONFIGDIR=str(root/"cache/matplotlib"),
               NUMBA_CACHE_DIR=str(root/"cache/numba"))
    return env


def stop_process(proc: subprocess.Popen | None) -> None:
    """Terminate only an owned process group, including inference/AX workers."""
    if proc is None:
        return
    try:
        os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:
        pass


def free_port(port: int) -> bool:
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            s.bind(("127.0.0.1",port))
        return True
    except OSError:
        return False


def supervise(args) -> int:
    root, resources = args.root.resolve(), args.resources.resolve()
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(root,0o700)
    lock = (root/"desktop.lock").open("a+")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return 2
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    state_path = root/"desktop-state.json"
    prefs = json.loads((root/"desktop.json").read_text()) if (root/"desktop.json").exists() else {}
    env = bundle_environment(resources,root,args.ollama_port)
    state = {"phase":"starting", "service_running":False,"ollama_running":False,
             "port":args.port,"root":str(root),"ble_address":prefs.get("ble_address")}
    write_private(state_path,json.dumps(state).encode())
    if not free_port(args.port) or not free_port(args.ollama_port):
        state.update(phase="failed",error="服务端口被占用，请停止其他 Desk-Mate 实例后重试")
        write_private(state_path,json.dumps(state).encode())
        return 3
    (root/"logs").mkdir(exist_ok=True)
    quitting = False
    def on_signal(*_):
        nonlocal quitting
        quitting = True
    signal.signal(signal.SIGTERM,on_signal)
    signal.signal(signal.SIGINT,on_signal)
    processes: dict[str,subprocess.Popen] = {}
    parent_pid = os.getppid()
    handles = []
    retries = {"ollama":0,"service":0}
    next_start = {"ollama":0.0,"service":0.0}
    def start(name,command):
        output = (root/"logs"/f"{name}.log").open("ab")
        handles.append(output)
        child_env = dict(env)
        if name == "ollama":
            child_env["HOME"] = str(root/"runtime-home")
            Path(child_env["HOME"]).mkdir(exist_ok=True,mode=0o700)
        processes[name] = subprocess.Popen(command,env=child_env,stdin=subprocess.DEVNULL,
                                            stdout=output,stderr=output,start_new_session=True,cwd=root)
    backend = [sys.executable,"-m","voice_assistant.app_runtime","--resources",str(resources),
               "--root",str(root),"--port",str(args.port),"backend"]
    if prefs.get("ble_address"):
        backend += ["--ble-address",prefs["ble_address"]]
    commands = {"ollama":[str(resources/"ollama/ollama"),"serve"], "service":backend}
    try:
        while not quitting:
            if os.getppid() != parent_pid:
                break  # Native app exited/crashed; do not leave workers behind.
            for name,command in commands.items():
                proc = processes.get(name)
                if proc is not None and proc.poll() is not None:
                    code = proc.returncode
                    stop_process(proc)
                    processes.pop(name)
                    retries[name] += 1
                    next_start[name] = time.monotonic()+min(30,2**retries[name])
                    label="本地模型服务" if name == "ollama" else "语音服务"
                    state.update(error=f"{label}异常退出（代码 {code}），正在尝试恢复（{min(retries[name],3)}/3）")
                if name not in processes and retries[name] <= 3 and time.monotonic() >= next_start[name]:
                    try:
                        start(name,command)
                    except OSError:
                        retries[name]=4
                        label="本地模型服务" if name == "ollama" else "语音服务"
                        state.update(error=f"无法启动{label}，请检查安装包是否完整，并查看诊断日志")
            state.update(service_running="service" in processes,ollama_running="ollama" in processes,
                         phase="starting",updated_at=time.time(),
                         pids={name:proc.pid for name,proc in processes.items()})
            if any(n>3 for n in retries.values()):
                state["phase"] = "failed"
                state["error"]="服务多次启动失败，已停止自动恢复。请查看日志，检查安装包或端口占用后重新启动服务。"
            try:
                ca = root/"private/ca.pem"
                if ca.exists():
                    tls = ssl.create_default_context(cafile=str(ca))
                    tls.check_hostname = False  # Loopback address; certificate still pinned to this installation's CA.
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=tls))
                    with opener.open(f"https://127.0.0.1:{args.port}/healthz",timeout=2) as response:
                        health = json.load(response)
                    state["health"] = health
                    failed=any(n>3 for n in retries.values())
                    state["phase"] = "failed" if failed else ("ready" if health.get("ready") else "degraded")
                    if health.get("ready") and not failed:
                        state.pop("error",None)
            except Exception:
                state.pop("health",None)
            write_private(state_path,json.dumps(state,ensure_ascii=False).encode())
            for _ in range(10):
                if quitting:
                    break
                time.sleep(.2)
    finally:
        for proc in processes.values():
            stop_process(proc)
        for handle in handles:
            handle.close()
        state.update(phase="stopped",service_running=False,ollama_running=False,updated_at=time.time(),pids={})
        state.pop("health",None)
        write_private(state_path,json.dumps(state,ensure_ascii=False).encode())
        lock.close()
    return 0


async def scan_devices() -> list[dict]:
    from bleak import BleakScanner
    from .ble_wire import SERVICE
    devices = {}
    async with BleakScanner(service_uuids=[SERVICE]) as scanner:
        await asyncio.sleep(5)
        for d in scanner.discovered_devices:
            devices[d.address] = {"address":d.address,"name":d.name or "Desk-Mate"}
        if sys.platform == "darwin":
            from CoreBluetooth import CBUUID
            manager = scanner._backend._manager.central_manager
            for peer in manager.retrieveConnectedPeripheralsWithServices_([
                CBUUID.UUIDWithString_(u) for u in (SERVICE,"1812","180f","180a")]):
                address = str(peer.identifier().UUIDString())
                devices[address] = {"address":address,"name":str(peer.name() or "Bluetooth device")}
    return list(devices.values())


def select_device(root,address,name=""):
    from .desktop_control import request_selection,validate_address,failure
    try:
        address=validate_address(address)
    except ValueError as exc:
        return failure("INVALID_DEVICE",str(exc))
    result=request_selection(root,address,name)
    if result.get("code") != "SERVICE_UNAVAILABLE":
        return result
    # Only save offline when the supervisor lock is actually free. During
    # startup, saving a preference would race the backend's initial selection.
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/"desktop.lock").open("a+") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return failure("SERVICE_STARTING","服务尚未准备好接收设备切换","请等模型加载完成后重试；无需停止服务")
        path=root/"desktop.json"
        prefs=json.loads(path.read_text()) if path.exists() else {}
        prefs.update(ble_address=address,ble_name=name[:80])
        write_private(path,json.dumps(prefs,ensure_ascii=False).encode())
    return {"ok":True,"code":"BLE_SAVED","address":address,"message":"已保存设备设置",
            "hint":"启动服务后会自动连接所选设备" if address else "蓝牙输入已设为断开"}


def export_diagnostics(root: Path, resources: Path, output: Path) -> None:
    """Export bounded operational evidence, excluding databases/audio/secrets."""
    with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for source,label in ((root/"desktop-state.json","state.json"),
                             (resources/"bundle.json","bundle.json"),
                             (root/"desktop.log","desktop.log"),
                             (root/"logs/service.log","service.log"),
                             (root/"logs/ollama.log","ollama.log")):
            if source.is_file() and not source.is_symlink():
                with source.open("rb") as stream:
                    stream.seek(max(0,source.stat().st_size-256*1024))
                    archive.writestr(label,stream.read())
    os.chmod(output,0o600)


def usb_pair_result(summary: dict) -> dict:
    # A stored USB registration is history, not proof that the selected board
    # currently holds the credential or has an authenticated live connection.
    if summary.get("unchanged"):
        result={"ok":True,"code":"USB_ALREADY_REGISTERED","message":"已有本机授权记录",
                "hint":"未打开 USB 或更换凭据。请使用重新连接；若设备端授权已清空，可在高级设置中重新授权。",
                "data":summary}
    else:
        result={"ok":True,"code":"USB_PAIRED","message":"USB 授权已写入设备",
                "hint":"请查看连接状态。只有首次连接才需在设备上选择这台 Mac；已配对设备可使用“重新连接”，无需再次授权。",
                "data":summary}
    if summary.get("warning"):
        result["detail"]=str(summary["warning"])
    return result


def operation_error(command: str, exc: Exception) -> dict:
    if command == "scan":
        from .ble_bridge import connection_error
        info=connection_error(exc,"scanning")
        return {"ok":False,"code":info["error_code"],"message":info["message"],"hint":info["hint"]}
    code=getattr(exc,"code","USB_PAIR_FAILED") if command == "pair" else "OPERATION_FAILED"
    if code == "COMMAND_BUSY":
        message,hint="正在处理语音任务，暂时不能更改设备授权","请等当前录音、执行或确认结束后再试；无需重启服务"
    elif code == "USB_AUTH_HISTORY_INVALID":
        message,hint="本机 USB 授权记录无法读取","原记录和设备凭据已保留。请先检查诊断信息，不要反复重新授权。"
    elif code == "USB_AUTH_SAVE_FAILED":
        message,hint="设备已接收新凭据，但本机授权记录保存失败","请先修复本机存储问题，再到高级设置重新授权；不要反复点击添加设备。"
    else:
        message="USB 授权未完成" if command == "pair" else "操作未能完成"
        hint="请检查 USB 数据线、设备唤醒状态及端口是否被其他程序占用" if command == "pair" else "请查看诊断状态并重试"
    return {"ok":False,"code":code,"message":message,"hint":hint,"detail":str(exc)[:180]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--resources",type=Path,required=True)
    p.add_argument("--root",type=Path,default=DEFAULT_ROOT)
    p.add_argument("--port",type=int,default=18765)
    p.add_argument("--ollama-port",type=int,default=11435)
    sub = p.add_subparsers(dest="command",required=True)
    sub.add_parser("serve")
    b = sub.add_parser("backend")
    b.add_argument("--ble-address")
    sub.add_parser("scan")
    cdp=sub.add_parser("enable-cdp")
    cdp.add_argument("--confirm-restart",action="store_true")
    selection=sub.add_parser("select-device")
    selection.add_argument("--address")
    selection.add_argument("--name",default="")
    sub.add_parser("reconnect")
    export = sub.add_parser("export")
    export.add_argument("--output",type=Path,required=True)
    pair = sub.add_parser("pair")
    pair.add_argument("--serial-port",required=True)
    pair.add_argument("--force",action="store_true",help="Explicitly replace this USB device's current-server credential")
    args = p.parse_args(argv)
    if args.command == "serve":
        return supervise(args)
    os.environ["VOICE_ASSISTANT_DATA_DIR"] = str(args.root)
    if args.command == "backend":
        from .api import run_server
        cfg = load_config()
        cfg.port = args.port
        run_server(cfg,ble_address=args.ble_address)
    elif args.command == "scan":
        print(json.dumps({"ok":True,"devices":asyncio.run(scan_devices())},ensure_ascii=False))
    elif args.command == "enable-cdp":
        from .adapters.cdp_player import enable_control
        result=enable_control(confirmed_restart=args.confirm_restart)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result["ok"] else 1
    elif args.command == "select-device":
        result=select_device(args.root,args.address,args.name)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result["ok"] else 1
    elif args.command == "reconnect":
        from .desktop_control import request_reconnect
        result=request_reconnect(args.root)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result["ok"] else 1
    elif args.command == "pair":
        from .provisioning import pair_device
        from .storage import Database
        cfg = load_config()
        cfg.port = args.port
        db = Database(cfg.db_path)
        try:
            result=pair_device(cfg,db,args.serial_port,"","",force=args.force)
            print(json.dumps(usb_pair_result(result),ensure_ascii=False))
        finally:
            db.close()
    elif args.command == "export":
        export_diagnostics(args.root,args.resources,args.output)
        print(json.dumps({"ok":True,"path":str(args.output)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        command=next((arg for arg in sys.argv if arg in ("scan","pair","select-device","reconnect","enable-cdp")),None)
        if command is None:
            raise
        print(json.dumps(operation_error(command,exc),ensure_ascii=False))
        raise SystemExit(1)
