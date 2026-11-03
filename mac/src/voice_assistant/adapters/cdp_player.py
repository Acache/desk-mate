"""Local Chrome DevTools Protocol client for the Bilibili PC player.

The client talks to ``127.0.0.1:9222`` only (never the network) and is available
when Bilibili is started with ``--remote-debugging-port=9222``. It reads and
acts on the player page DOM directly: no OCR, no pixels, no screenshots.

Every method returns a dict with ``ok`` and the value it read back.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
import urllib.request
from urllib.parse import unquote

import websockets
from websockets.sync.client import connect

PORT = 9222
PLAYER_URL_MARK = "player.html"


class CdpError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _endpoint():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as resp:
            try:
                data=json.load(resp)
            except (ValueError,UnicodeError):
                return [],True,False
            return ([item for item in data if isinstance(item,dict)],True,True) if isinstance(data,list) else ([],True,False)
    except Exception:
        return [],False,False


def _targets() -> list[dict]:
    return _endpoint()[0]


def available() -> bool:
    return any(PLAYER_URL_MARK in (t.get("url") or "") for t in _targets())


def status() -> dict:
    targets,reachable,valid = _endpoint()
    return {"endpoint": f"127.0.0.1:{PORT}", "reachable": reachable,"target_count":len(targets),
            "unrecognized_endpoint":reachable and not valid,
            "cdp_ready": any(_bili_target(t) for t in targets),
            "player_available": any(PLAYER_URL_MARK in (t.get("url") or "") for t in targets)}


def _bili_target(target) -> bool:
    value=unquote(str(target.get("url") or "")).lower()
    return any(mark in value for mark in ("bilibili","哔哩哔哩",PLAYER_URL_MARK))


BUNDLE = "com.bilibili.bilibiliPC"
PROCESS_MARK = "哔哩哔哩.app/Contents/MacOS/哔哩哔哩"


def _application_info() -> dict:
    helper=Path(__file__).resolve().parents[3]/"bin/ax_helper"
    if helper.exists():
        try:
            output=subprocess.run([str(helper),"application-info"],capture_output=True,text=True,timeout=3)
            data=json.loads(output.stdout)
            if data.get("ok") and "installed" in data:
                return data
        except (OSError,ValueError,subprocess.TimeoutExpired):
            pass
    return {}


def app_running() -> bool:
    info=_application_info()
    if info:
        return bool(info.get("running"))
    try:
        out = subprocess.run(["pgrep", "-f", PROCESS_MARK], capture_output=True,
                             text=True, timeout=5)
        return bool(out.stdout.strip())
    except Exception:
        return False


def _app_installed() -> bool:
    info=_application_info()
    if info:
        return bool(info.get("installed"))
    if any((base/name).exists() for base in (Path("/Applications"),Path.home()/"Applications")
           for name in ("哔哩哔哩.app","Bilibili.app")):
        return True
    try:
        output=subprocess.run(["mdfind",f"kMDItemCFBundleIdentifier == '{BUNDLE}'"],
                              capture_output=True,text=True,timeout=3)
        return bool(output.stdout.strip())
    except (OSError,subprocess.TimeoutExpired):
        return False


def _debug_flag_present() -> bool:
    try:
        info=_application_info()
        pids=[str(info["pid"])] if info.get("pid") else subprocess.run(["pgrep","-f",PROCESS_MARK],capture_output=True,text=True,timeout=3).stdout.split()
        if not pids:
            return False
        command=subprocess.run(["ps","-p",",".join(pids),"-o","command="],capture_output=True,text=True,timeout=3).stdout
        return "--remote-debugging-port=9222" in command
    except (OSError,subprocess.TimeoutExpired):
        return False


def enable_control(confirmed_restart=False,timeout=12.0) -> dict:
    """Open or connect; a running client is restarted only after UI consent."""
    def result(ok,code,message,hint="",**extra):
        return {"ok":ok,"code":code,"message":message,"hint":hint,**extra}
    def ready(snapshot):
        return result(True,"BILI_PLAYER_READY" if snapshot["player_available"] else "BILI_CDP_READY",
                      "B站播放器已连接" if snapshot["player_available"] else "B站已打开，本地控制已启用",
                      "" if snapshot["player_available"] else "在 B站打开一个视频后即可使用播放器语音控制",status=snapshot)
    def foreign(snapshot):
        return snapshot["reachable"] and (snapshot.get("unrecognized_endpoint") or snapshot.get("target_count",1)>0)
    snapshot=status()
    if snapshot["cdp_ready"]:
        return ready(snapshot)
    if foreign(snapshot):
        return result(False,"CDP_PORT_IN_USE","本地控制端口 9222 被其他程序占用","请关闭使用该调试端口的程序后重试；不会重启其他程序")
    running=app_running()
    if not running and not _app_installed():
        return result(False,"BILI_NOT_INSTALLED","未找到 B站 PC 客户端","请先安装 B站 PC 客户端，再点击此入口")
    if running and not confirmed_restart and not _debug_flag_present():
        return result(False,"BILI_RESTART_REQUIRED","B站正在运行，但尚未启用本地控制",
                      "需要重启 B站并中断当前播放，确认后才会继续",requires_restart=True)
    try:
        if running and confirmed_restart:
            pid=_application_info().get("pid")
            stop=["kill","-TERM",str(pid)] if pid else ["pkill","-TERM","-f",PROCESS_MARK]
            subprocess.run(stop,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            quit_deadline=time.monotonic()+5
            while app_running() and time.monotonic()<quit_deadline:
                time.sleep(.1)
            if app_running():
                return result(False,"BILI_QUIT_TIMEOUT","B站未能正常退出","请手动退出 B站后重试；没有强制终止播放进程")
            running=False
        if not running:
            launch=subprocess.run(["open","-b",BUNDLE,"--args","--remote-debugging-port=9222"],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            if launch.returncode:
                return result(False,"BILI_LAUNCH_FAILED","无法启动 B站客户端","请确认客户端可手动打开，然后重试")
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            snapshot=status()
            if snapshot["cdp_ready"]:
                return ready(snapshot)
            if foreign(snapshot):
                return result(False,"CDP_PORT_IN_USE","端口 9222 未提供 B站控制页面","请查看诊断状态，确认没有其他程序占用端口")
            time.sleep(.25)
        return result(False,"BILI_CDP_TIMEOUT","B站尚未开放本地控制连接",
                      "请等客户端完成启动后重试；若仍失败，可确认重启以重新启用本地控制",requires_restart=app_running())
    except (OSError,subprocess.TimeoutExpired):
        return result(False,"BILI_OPERATION_FAILED","启动或连接 B站失败","请确认客户端正常运行，查看诊断后重试")


def ensure(restart: bool = False, timeout: float = 12.0) -> bool:
    """Make the local DevTools port available.

    ``restart=True`` gracefully restarts Bilibili with the debugging flag when
    it is running without it; this interrupts playback and must stay a
    user-initiated action in the packaged app.
    """
    if available():
        return True
    if not restart or not app_running():
        return available()
    subprocess.run(["pkill", "-TERM", "-f", PROCESS_MARK], capture_output=True, timeout=5)
    for _ in range(20):
        if not app_running():
            break
        time.sleep(0.25)
    subprocess.run(["open", "-b", BUNDLE, "--args", "--remote-debugging-port=9222"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if available():
            return True
        time.sleep(0.3)
    return available()


def player_ws() -> str | None:
    for target in _targets():
        if PLAYER_URL_MARK in (target.get("url") or ""):
            return target.get("webSocketDebuggerUrl")
    return None


class CdpPlayer:
    """One short-lived WebSocket session per action (connect ~5 ms)."""

    def __init__(self, ws_url: str | None = None, timeout: float = 4.0) -> None:
        self.ws_url = ws_url or player_ws()
        if not self.ws_url:
            raise CdpError("NO_PLAYER_TARGET", "B站播放页没有暴露调试端口")
        self.timeout = timeout
        self._next_id = 1

    def __enter__(self) -> "CdpPlayer":
        self._ws = connect(self.ws_url, open_timeout=self.timeout,
                           max_size=16 * 1024 * 1024).__enter__()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._ws.__exit__(*exc)
        except Exception:
            pass

    def js(self, expression: str):
        msg_id = self._next_id
        self._next_id += 1
        self._ws.send(json.dumps({
            "id": msg_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "awaitPromise": True},
        }))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            message = json.loads(self._ws.recv())
            if message.get("id") != msg_id:
                continue
            result = message.get("result", {})
            if "exceptionDetails" in result:
                raise CdpError("JS_ERROR", str(result["exceptionDetails"])[:200])
            return result.get("result", {}).get("value")
        raise CdpError("CDP_TIMEOUT", "CDP 调用超时")

    def call(self, method: str, params: dict | None = None):
        msg_id = self._next_id
        self._next_id += 1
        self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            message = json.loads(self._ws.recv())
            if message.get("id") == msg_id:
                return message.get("result", {})
        raise CdpError("CDP_TIMEOUT", method)

    def mouse_move(self, x: float, y: float) -> None:
        self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})

    # --- queries ---------------------------------------------------------
    def state(self) -> dict:
        raw = self.js(r"""(() => {
          const v=document.querySelector('video');
          const cls=el=>el?el.className.toString():'';
          const txt=el=>el?el.textContent.trim():'';
          return JSON.stringify({
            href: location.href,
            video: !!v,
            time: v?v.currentTime:null,
            duration: v?v.duration:null,
            paused: v?v.paused:null,
            volume: v?v.volume:null,
            like: cls(document.querySelector('.like')),
            like_count: txt(document.querySelector('.like .info-text')),
            coin: cls(document.querySelector('.coin')),
            coin_count: txt(document.querySelector('.coin .info-text')),
            collect: cls(document.querySelector('.collect')),
            collect_count: txt(document.querySelector('.collect .info-text')),
          });
        })()""")
        return json.loads(raw or "{}")

    def time(self) -> float | None:
        return self.js("(document.querySelector('video')||{}).currentTime ?? null")

    def duration(self) -> float | None:
        return self.js("(document.querySelector('video')||{}).duration ?? null")

    def title(self) -> str:
        return str(self.js("document.title || ''") or "")

    # --- actions ---------------------------------------------------------
    def like(self) -> dict:
        before = self.state()
        if "on" in str(before.get("like", "")):
            return {"ok": True, "changed": False, "state": "liked", "via": "cdp"}
        self.js("document.querySelector('.like').click(); 1")
        time.sleep(0.4)
        after = self.state()
        ok = "on" in str(after.get("like", ""))
        return {"ok": ok, "changed": ok, "state": "liked", "via": "cdp",
                "count": after.get("like_count")}

    def coin(self, count: int = 1) -> dict:
        before = self.state()
        self.js("document.querySelector('.coin').click(); 1")
        time.sleep(0.6)
        dialog = self.js(r"""(() => {
          const d=document.querySelector('.app-cionpanel--btn-sure');
          const opts=[...document.querySelectorAll('.bili-dialog-m span, .app-cionpanel--coin span')]
            .map(e=>e.textContent.trim()).filter(t=>/^[12]个$/.test(t));
          return JSON.stringify({confirm: !!d, options: opts});
        })()""")
        info = json.loads(dialog or "{}")
        if not info.get("confirm"):
            tip = self.js(r"""(() => {
              const t=document.body.innerText||'';
              return /已用完|投币枚数/.test(t) ? 'coin_limit' : 'dialog_not_open';
            })()""")
            return {"ok": False, "error": tip or "dialog_not_open"}
        # The picker defaults to 1 coin; select the requested one when present.
        self.js(f"""(() => {{
          const opts=[...document.querySelectorAll('.bili-dialog-m span, .app-cionpanel--coin span')]
            .filter(e=>e.textContent.trim()==='{count}个');
          if (opts.length) opts[0].click();
          return 1;
        }})()""")
        time.sleep(0.2)
        self.js("document.querySelector('.app-cionpanel--btn-sure').click(); 1")
        time.sleep(0.8)
        after = self.state()
        changed = after.get("coin_count") != before.get("coin_count") or "on" in str(after.get("coin", ""))
        return {"ok": changed, "count": count, "before": before.get("coin_count"),
                "after": after.get("coin_count"), "via": "cdp"}

    def favorite(self, folder: str | None = None) -> dict:
        before = self.state()
        self.js("document.querySelector('.collect').click(); 1")
        time.sleep(0.6)
        if folder:
            clicked = self.js(f"""(() => {{
              const items=[...document.querySelectorAll('.app-collection--list-item')];
              const target=items.find(e=>e.textContent.includes({json.dumps(folder)}));
              if (!target) return false;
              target.click();
              return true;
            }})()""")
            if not clicked:
                self.js("document.querySelector('.bili-dialog-m')?.remove(); 1")
                return {"ok": False, "error": "folder_not_found"}
            time.sleep(0.3)
        saved = self.js(r"""(() => {
          const b=document.querySelector('button.btn.submit-move') ||
                  [...document.querySelectorAll('button')].find(e=>/保存/.test(e.textContent));
          if (!b) return false; b.click(); return true;
        })()""")
        if not saved:
            return {"ok": False, "error": "save_button_not_found"}
        time.sleep(0.8)
        after = self.state()
        return {"ok": True, "folder": folder or "默认收藏夹", "via": "cdp",
                "before": before.get("collect_count"), "after": after.get("collect_count")}

    def seek_to(self, seconds: float) -> dict:
        duration = self.duration()
        target = max(0.0, min(float(seconds), float(duration or seconds)))
        self.js(f"document.querySelector('video').currentTime={target}; 1")
        time.sleep(0.25)
        return {"ok": True, "target": target, "after": self.time(), "via": "cdp"}

    def seek_by(self, seconds: float) -> dict:
        current = self.time()
        if current is None:
            return {"ok": False, "error": "no_video"}
        return self.seek_to(current + float(seconds))

    def _shortcut(self, key: str, code: str) -> None:
        # The player titles advertise < / > for previous/next part; the visible
        # 多P buttons stay 0-width and their .click() is unreliable.
        self.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "text": key,
                                             "code": code, "modifiers": 8})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code,
                                             "modifiers": 8})

    def next_episode(self) -> dict:
        before = self.state().get("href")
        self._shortcut(">", "Period")
        time.sleep(1.2)
        after = self.state().get("href")
        if after != before:
            return {"ok": True, "before": before, "after": after, "via": "shortcut"}
        clicked = self.js(r"""(() => {
          const b=[...document.querySelectorAll('[title*="下一P"],[title*="下一集"],[class*="next"]')]
            .find(e=>e.getBoundingClientRect().width>0);
          if (!b) return false; b.click(); return true;
        })()""")
        time.sleep(1.2)
        after = self.state().get("href")
        return {"ok": bool(clicked) and after != before, "before": before, "after": after}

    def prev_episode(self) -> dict:
        before = self.state().get("href")
        self._shortcut("<", "Comma")
        time.sleep(1.2)
        after = self.state().get("href")
        if after != before:
            return {"ok": True, "before": before, "after": after, "via": "shortcut"}
        clicked = self.js(r"""(() => {
          const b=[...document.querySelectorAll('[title*="上一P"],[title*="上一集"]')]
            .find(e=>e.getBoundingClientRect().width>0);
          if (!b) return false; b.click(); return true;
        })()""")
        time.sleep(1.2)
        after = self.state().get("href")
        return {"ok": bool(clicked) and after != before, "before": before, "after": after}

    def danmaku(self, text: str) -> dict:
        # The sending area only renders while the player controls are visible.
        self.mouse_move(350, 520)
        time.sleep(0.5)
        found = self.js("!!document.querySelector('.bpx-player-dm-input')")
        if not found:
            return {"ok": False, "error": "danmaku_input_not_found"}
        typed = self.js(f"""(() => {{
          const i=document.querySelector('.bpx-player-dm-input');
          i.focus();
          i.value={json.dumps(text)};
          i.dispatchEvent(new Event('input', {{bubbles:true}}));
          return i.value;
        }})()""")
        time.sleep(0.2)
        self.js(r"""(() => {
          const i=document.querySelector('.bpx-player-dm-input');
          const opts={bubbles:true, cancelable:true, key:'Enter', code:'Enter', keyCode:13, which:13};
          i.dispatchEvent(new KeyboardEvent('keydown', opts));
          i.dispatchEvent(new KeyboardEvent('keyup', opts));
          return true;
        })()""")
        time.sleep(0.6)
        after = self.js("(document.querySelector('.bpx-player-dm-input')||{}).value ?? null")
        return {"ok": after == "", "typed": typed, "after": after, "via": "cdp"}

    def home(self) -> dict:
        """Return the player window to the Bilibili home page (DOM click)."""
        clicked = self.js(r"""(() => {
          const b=[...document.querySelectorAll('[title*="回到主界面"],[class*="header-home"]')]
            .find(e=>e.getBoundingClientRect().width>0);
          if (!b) return false;
          b.click();
          return true;
        })()""")
        time.sleep(1.2)
        href = self.state().get("href", "")
        return {"ok": bool(clicked) and "player.html" not in href, "href": str(href)[:80],
                "via": "cdp"}

    def comments(self, open_comments: bool = True) -> dict:
        tab = "评论" if open_comments else "简介"
        clicked = self.js(f"""(() => {{
          const t=[...document.querySelectorAll('.app_player--toggle-btn, [class*="tab"]')]
            .find(e=>e.textContent.trim().startsWith({json.dumps(tab)}));
          if (!t) return false; t.click(); return true;
        }})()""")
        time.sleep(0.3)
        return {"ok": bool(clicked), "open": open_comments, "via": "cdp"}
