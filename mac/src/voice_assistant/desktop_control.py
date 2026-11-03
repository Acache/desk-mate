"""User-only local BLE control. No HTTP management endpoint is exposed."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import time
from uuid import UUID

from .config import write_private


def control_path(root: Path) -> Path:
    # macOS limits Unix socket paths to about 104 bytes; do not embed a long
    # Application Support or test-directory path in the socket name.
    identity = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
    return Path("/tmp")/f"desk-mate-{os.getuid()}-{identity}.sock"


def validate_address(address):
    if address is None:
        return None
    if not isinstance(address,str):
        raise ValueError("设备地址无效，请重新扫描并选择设备")
    try:
        return str(UUID(address)).upper()
    except ValueError:
        if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}",address):
            return address.upper()
    raise ValueError("设备地址无效，请重新扫描并选择设备")


def failure(code, message, hint=""):
    return {"ok":False,"code":code,"message":message,"hint":hint}


class BleController:
    def __init__(self,state,connector=None):
        if connector is None:
            from .ble_bridge import run_connector
            connector = run_connector
        self.state,self.connector = state,connector
        self.task = None
        self.address = None
        self.lock = asyncio.Lock()
        self.closed = False

    async def select(self,address,name="",persist=True):
        try:
            address=validate_address(address)
        except ValueError as exc:
            return failure("INVALID_DEVICE",str(exc))
        async with self.lock:
            if self.closed:
                return failure("SERVICE_STOPPING","服务正在停止","请等停止完成后再启动服务")
            if address == self.address and self.task is not None and not self.task.done() and not self.task.cancelling():
                return {"ok":True,"code":"BLE_ALREADY_SELECTED","address":address,
                        "message":"正在维护该设备连接","authenticated":bool(self.state.ble_status.get("authenticated"))}
            if self.state.db.active_command():
                return failure("COMMAND_BUSY","正在处理语音指令，暂时不能切换设备","请等当前录音、执行或确认结束后重试")
            self.state.ble_switching=True
            try:
                self.state.ble_status.update(phase="switching",authenticated=False)
                if self.task is not None:
                    self.task.cancel()
                    done,_=await asyncio.wait({self.task},timeout=5)
                    if not done:
                        result=failure("BLE_DISCONNECT_TIMEOUT","旧设备尚未完成断开","请稍后重试；语音和决策模型无需重启")
                        self.state.ble_status.update(phase="retry",error_code=result["code"],message=result["message"],hint=result["hint"])
                        return result
                    await asyncio.gather(self.task,return_exceptions=True)
                    self.task=None
                if persist:
                    path=self.state.cfg.root/"desktop.json"
                    try:
                        prefs=json.loads(path.read_text()) if path.exists() else {}
                        prefs.update(ble_address=address,ble_name=name[:80])
                        write_private(path,json.dumps(prefs,ensure_ascii=False).encode())
                    except (OSError,ValueError) as exc:
                        self.address=None
                        self.state.ble_status={"enabled":False,"phase":"disconnected","authenticated":False}
                        return failure("SETTINGS_WRITE_FAILED","无法保存设备设置","请检查应用数据目录的磁盘空间与写入权限")
                self.address=address
                self.state.ble_status={"enabled":bool(address),"phase":"connecting" if address else "disconnected",
                                       "authenticated":False,"address":address,"device_name":name[:80]}
                if address:
                    self.task=asyncio.create_task(self._connect(address))
                return {"ok":True,"code":"BLE_CONNECTING" if address else "BLE_DISCONNECTED",
                        "address":address,"message":"正在连接并认证设备" if address else "已断开蓝牙输入",
                        "authenticated":False}
            finally:
                # A stuck old disconnect must not reopen the begin gate while
                # its transport is still cancelling. A subsequent retry can
                # finish the handover without restarting any model.
                self.state.ble_switching=bool(self.task and not self.task.done() and self.task.cancelling())

    async def _connect(self,address):
        try:
            await self.connector(self.state,address)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from .ble_bridge import connection_error
            self.state.ble_status.update(phase="retry",authenticated=False,**connection_error(exc,"connecting"))

    async def close(self):
        async with self.lock:
            self.closed=True
            if self.task is not None:
                self.task.cancel()
                await asyncio.gather(self.task,return_exceptions=True)


async def start_control_server(state):
    path=control_path(state.cfg.root)
    if path.exists() or path.is_symlink():
        info=path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError("本机控制通道路径被占用，请查看诊断日志")
        try:
            reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(str(path)),1)
        except (ConnectionRefusedError,FileNotFoundError):
            path.unlink(missing_ok=True)
        else:
            writer.close(); await writer.wait_closed()
            raise RuntimeError("同一数据目录已有控制服务，请勿重复启动")

    async def handle(reader,writer):
        try:
            raw=await asyncio.wait_for(reader.readline(),3)
            if len(raw)>2048:
                raise ValueError("控制请求过长")
            request=json.loads(raw)
            if not isinstance(request,dict) or "address" not in request or set(request)-{"action","address","name"} or request.get("action") != "select_ble":
                raise ValueError("不支持的本机控制请求")
            name=request.get("name","")
            if not isinstance(name,str) or len(name)>80:
                raise ValueError("设备名称无效")
            result=await state.ble_controller.select(request.get("address"),name)
        except (ValueError,asyncio.TimeoutError) as exc:
            result=failure("INVALID_REQUEST","本机控制请求无效或超时","请重新选择设备")
        except Exception:
            result=failure("CONTROL_ERROR","切换设备失败","请查看诊断日志后重试")
        try:
            writer.write(json.dumps(result,ensure_ascii=False).encode()+b"\n")
            await writer.drain()
        except (BrokenPipeError,ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    server=await asyncio.start_unix_server(handle,path=str(path),limit=2048)
    os.chmod(path,0o600)
    return server,path


def request_selection(root,address,name="",timeout=8):
    try:
        address=validate_address(address)
    except ValueError as exc:
        return failure("INVALID_DEVICE",str(exc))
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(control_path(root)))
            client.sendall(json.dumps({"action":"select_ble","address":address,"name":name},ensure_ascii=False).encode()+b"\n")
            data=b""
            while b"\n" not in data and len(data)<4096:
                chunk=client.recv(4096-len(data))
                if not chunk:
                    break
                data+=chunk
            return json.loads(data)
    except (OSError,ValueError):
        return failure("SERVICE_UNAVAILABLE","无法联系正在运行的服务","请先启动服务，等模型加载完成后再切换设备")
