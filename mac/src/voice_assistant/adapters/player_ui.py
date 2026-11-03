"""Pixel-verified controls for the Bilibili desktop player.

The PC client renders its player controls in a CEF view that exposes almost
nothing through Accessibility: only play/pause are addressable by name.
Everything else (like/coin/favorite/comment/danmaku/seek bar) is clicked by
position and verified by looking at the screen: pink like icon, gold star,
coin counter, OCR of the folder picker, danmaku composer clearing.

Every public function returns a dict with ``ok`` and a read-back field; callers
must not report success without it.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import cdp_player

MAC_DIR = Path(__file__).resolve().parents[3]
HELPER = MAC_DIR / "bin" / "ax_helper"
BILI_BUNDLE = "com.bilibili.bilibiliPC"

SEEK_STEP_SECONDS = 5  # measured: one arrow press moves 4-6 s


class PlayerUiError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _run(args: list[str], timeout: float, capture: bool = True) -> subprocess.CompletedProcess[str]:
    # Never inherit a pipe a grandchild can keep open: `open -b` spawns
    # LaunchServices helpers and subprocess.run would then wait for EOF long
    # after the child exited (observed as multi-minute "hangs").
    kwargs: dict = {"stdin": subprocess.DEVNULL, "timeout": timeout, "start_new_session": True}
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run(args, **kwargs)


def helper(*args: str, timeout: float = 10.0) -> dict:
    proc = _run([str(HELPER), *args], timeout)
    if proc.returncode != 0:
        raise PlayerUiError("HELPER_FAILED", (proc.stderr or proc.stdout or "")[:200])
    import json

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PlayerUiError("HELPER_BAD_JSON", proc.stdout[:200]) from exc
    if not data.get("ok", True):
        raise PlayerUiError(str(data.get("error") or "HELPER_NOT_OK"))
    return data


def activate() -> None:
    try:
        _run(["open", "-b", BILI_BUNDLE], 6, capture=False)
    except Exception:
        pass
    time.sleep(0.5)
    for _ in range(6):
        try:
            if helper("activate", timeout=6).get("activated"):
                break
        except Exception:
            pass
        time.sleep(0.3)
    time.sleep(0.3)


def click(x: float, y: float) -> None:
    helper("click-at", "--x", str(int(x)), "--y", str(int(y)), timeout=6)


def press_key(name: str) -> None:
    helper("key", name, timeout=6)


def type_text(text: str) -> None:
    helper("type", text, timeout=10)


def wait(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True)
class PlayerFrame:
    x: int
    y: int
    w: int
    h: int
    time_text: str = ""

    @property
    def like(self) -> tuple[int, int]:
        return self.x + self.w + 50, self.y + 199

    @property
    def coin(self) -> tuple[int, int]:
        return self.x + self.w + 120, self.y + 199

    @property
    def favorite(self) -> tuple[int, int]:
        return self.x + self.w + 190, self.y + 199

    @property
    def play(self) -> tuple[int, int]:
        return self.x + 24, self.y + 579

    @property
    def next_episode(self) -> tuple[int, int]:
        return self.x + 56, self.y + 579

    @property
    def danmaku_toggle(self) -> tuple[int, int]:
        return self.x + 251, self.y + 579

    @property
    def comments(self) -> tuple[int, int]:
        return self.x + 283, self.y + 579

    @property
    def danmaku_input(self) -> tuple[int, int]:
        return self.x + 343, self.y + 570

    @property
    def volume(self) -> tuple[int, int]:
        return self.x + 626, self.y + 571

    @property
    def progress(self) -> tuple[int, int, int]:
        return self.x + 12, self.y + 544, self.w - 24


def player_frame() -> PlayerFrame | None:
    data = helper("player", timeout=10)
    frame = data.get("frame") or {}
    if not frame:
        return None
    try:
        return PlayerFrame(int(frame["x"]), int(frame["y"]), int(frame["w"]), int(frame["h"]),
                           str(data.get("time") or ""))
    except (KeyError, TypeError, ValueError):
        return None


def require_frame() -> PlayerFrame:
    frame = player_frame()
    if frame is None:
        raise PlayerUiError("NO_PLAYER", "B站没有正在播放的视频")
    return frame


def grab(box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        raise PlayerUiError("BAD_REGION")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        _run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", path], 8, capture=False)
        return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    finally:
        Path(path).unlink(missing_ok=True)


def _pink(img: np.ndarray) -> np.ndarray:
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    return (r > 150) & (r - b > 40) & (r - g > 40)


def icon_active(frame: PlayerFrame, which: str) -> bool:
    """True when the like thumb or the favourite star is highlighted (pink)."""
    x, y = panel_points()[which]
    return int(_pink(grab((x - 16, y - 16, 32, 32))).sum()) > 120


def click_icon(frame: PlayerFrame, which: str) -> None:
    x, y = panel_points()[which]
    click(x, y)


def player_seconds(frame: PlayerFrame | None = None) -> int | None:
    data = helper("player", timeout=8)
    text = str(data.get("time") or "")
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


# --- OCR helpers ------------------------------------------------------------

FAV_DIALOG_BOX = (300, 120, 620, 560)
COIN_DIALOG_BOX = (300, 120, 620, 560)
DANMAKU_INPUT_BOX = (495, 786, 210, 30)


def ocr(box: tuple[int, int, int, int]) -> list[dict]:
    x, y, w, h = (str(int(v)) for v in box)
    data = helper("ocr", "--x", x, "--y", y, "--w", w, "--h", h, timeout=30)
    return list(data.get("rows") or [])


def region_text(box: tuple[int, int, int, int]) -> str:
    return " ".join(str(r.get("text", "")) for r in ocr(box)).strip()


def clock_seconds(text: str) -> list[int]:
    import re

    out: list[int] = []
    for match in re.finditer(r"(\d{1,2}):(\d{2})", text):
        out.append(int(match.group(1)) * 60 + int(match.group(2)))
    return out


def count_box(which: str) -> tuple[int, int, int, int]:
    x, y = panel_points()[which]
    return x - 21, y + 20, 42, 20


def count_at(box: tuple[int, int, int, int]) -> int | None:
    import re

    match = re.search(r"([0-9][0-9.,]*\s*[万]?)", region_text(box))
    if not match:
        return None
    raw = match.group(1).replace(",", "").replace(" ", "")
    try:
        return int(float(raw[:-1]) * 10000) if raw.endswith("万") else int(float(raw))
    except ValueError:
        return None


def click_text(needle: str, region: tuple[int, int, int, int]) -> bool:
    for row in ocr(region):
        if needle in str(row.get("text", "")):
            click(int(row["x"]) + int(row["w"]) / 2, int(row["y"]) + int(row["h"]) / 2)
            return True
    return False


def open_dialog(point: tuple[int, int], marker: str, region: tuple[int, int, int, int],
                attempts: int = 4) -> bool:
    """Click a control until the expected modal really shows up (focus-safe)."""
    for _ in range(attempts):
        activate()
        click(*point)
        wait(0.9)
        if marker in region_text(region):
            return True
    return False


MAIN_TITLE = "哔哩哔哩 (゜-゜)つロ 干杯~"


def player_window() -> tuple[int, int, int, int] | None:
    """The floating player window (video + the side info panel)."""
    for win in helper("cgwindows", timeout=6).get("windows") or []:
        name = str(win.get("name") or "")
        if MAIN_TITLE in name:
            continue
        b = win.get("bounds") or {}
        try:
            width, height = int(b["Width"]), int(b["Height"])
        except (KeyError, TypeError, ValueError):
            continue
        # Ignore tooltips/popups: the player window is at least a few hundred
        # points wide, otherwise panel offsets are meaningless.
        if width < 300 or height < 200 or int(win.get("layer", 0)) != 0:
            continue
        return int(b["X"]), int(b["Y"]), width, height
    return None


def panel_points() -> dict[str, tuple[int, int]]:
    """Like/coin/favourite row inside the floating player window.

    The row is anchored to the window's right edge (measured on the 1082x660
    window: like at right-330). If the window is too narrow the side panel is
    not rendered at all, so it is widened first.
    """
    win = player_window()
    if win is None:
        raise PlayerUiError("NO_PLAYER_WINDOW", "找不到 B 站播放窗口")
    x, y, w, h = win
    if w < 900:
        for other in helper("cgwindows", timeout=6).get("windows") or []:
            name = str(other.get("name") or "")
            if MAIN_TITLE in name:
                continue
            helper("set-window", "--name", name, "--w", "1082", "--h", "660", timeout=8)
            break
        wait(0.6)
        win = player_window() or win
        x, y, w, h = win
    right = x + w
    row = y + 248
    return {"like": (right - 330, row), "coin": (right - 260, row), "favorite": (right - 190, row)}


def cdp_session():
    """Local CDP session for the player page, or None when it is not enabled."""
    if not cdp_player.available():
        return None
    try:
        return cdp_player.CdpPlayer()
    except cdp_player.CdpError:
        return None


def press_dom(needle: str, role: str = "AXButton") -> bool:
    """Press a Chromium element by DOM id/class (reliable, no pixels)."""
    try:
        data = helper("press-dom", "--id", needle, "--role", role, timeout=8)
    except PlayerUiError:
        return False
    return bool(data.get("pressed"))


def shortcut(key: str) -> None:
    helper("key", key, timeout=6)


def bar_visible(frame: PlayerFrame) -> bool:
    """Reveal the auto-hidden control bar and verify its clock text is on screen."""
    for _ in range(2):
        helper("move-to", "--x", str(frame.x + frame.w // 2),
               "--y", str(frame.y + frame.h - 60), timeout=6)
        wait(0.45)
        if clock_seconds(region_text((frame.x + 30, frame.y + 568, 300, 34))):
            return True
    return False


def panel_ready() -> bool:
    """The side panel really is where we think: its counters must be readable."""
    try:
        return count_at(count_box("like")) is not None and count_at(count_box("coin")) is not None
    except PlayerUiError:
        return False


# --- actions ----------------------------------------------------------------

# NOTE: these offsets were measured when the dialog is centred on a 1470-wide
# main window. The dialog is centred on the *player window*, so they must be
# derived from the dialog's own OCR bounds (title "投币" / "选择收藏夹") before
# this can be called layout-independent. The like icon (panel_points) already is.
COIN_OPTION_1 = (610, 432)
COIN_OPTION_2 = (785, 432)


def like() -> dict:
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.like()
        except Exception:
            pass
    frame = require_frame()
    activate()
    if icon_active(frame, "like"):
        return {"ok": True, "changed": False, "state": "liked", "via": "state"}
    # 1) player shortcut Q (verified live), 2) DOM element, 3) verified pixel.
    activate()
    shortcut("q")
    wait(0.9)
    if icon_active(frame, "like"):
        return {"ok": True, "changed": True, "state": "liked", "via": "shortcut"}
    if press_dom("like") or press_dom("thumb-up"):
        wait(0.9)
        if icon_active(frame, "like"):
            return {"ok": True, "changed": True, "state": "liked", "via": "dom"}
    for _ in range(2):
        activate()
        if not panel_ready():
            wait(0.4)
            continue
        click_icon(frame, "like")
        wait(0.9)
        if icon_active(frame, "like"):
            return {"ok": True, "changed": True, "state": "liked", "via": "pixel"}
    return {"ok": False, "changed": False, "state": "failed"}


def coin(count: int = 1) -> dict:
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.coin(count)
        except Exception:
            pass
    if count not in (1, 2):
        raise PlayerUiError("BAD_ARGUMENT", "投币数量只能是 1 或 2")
    frame = require_frame()
    before = count_at(count_box("coin"))
    activate()
    if press_dom("coin"):
        wait(0.9)
    if "投币数量" not in region_text(COIN_DIALOG_BOX):
        activate()
        shortcut("w")
        wait(0.9)
    if "投币数量" not in region_text(COIN_DIALOG_BOX):
        if not open_dialog(panel_points()["coin"], "投币数量", COIN_DIALOG_BOX):
            # Bilibili refuses a third coin on the same video and shows a
            # tooltip instead of the dialog: report that, do not retry blindly.
            cx, cy = panel_points()["coin"]
            helper("move-to", "--x", str(cx), "--y", str(cy), timeout=6)
            wait(0.5)
            tip = region_text((cx - 160, cy + 10, 320, 60))
            press_key("escape")
            if "用完" in tip or "投币枚数" in tip:
                return {"ok": False, "error": "coin_limit_reached", "tip": tip[:40]}
            return {"ok": False, "error": "dialog_not_open"}
    # The dialog is centred on the player window, so its position follows the
    # window. Anchor the two option cards to the confirm button (OCR), not to
    # absolute pixels measured on an old layout.
    confirm = None
    for row in ocr(COIN_DIALOG_BOX):
        if "确认投币" in str(row.get("text", "")):
            confirm = row
            break
    if confirm is None:
        press_key("escape")
        return {"ok": False, "error": "confirm_button_not_found"}
    cx = float(confirm["x"]) + float(confirm["w"]) / 2
    cy = float(confirm["y"]) + float(confirm["h"]) / 2
    click(cx - 167 if count == 1 else cx + 7, cy - 242)
    wait(0.4)
    if not click_text("确认投币", COIN_DIALOG_BOX):
        press_key("escape")
        return {"ok": False, "error": "confirm_button_not_found"}
    wait(1.6)
    after = count_at(count_box("coin"))
    ok = before is not None and after is not None and after > before
    return {"ok": ok, "coins": count, "before": before, "after": after}


def favorite(folder: str | None = None) -> dict:
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.favorite(folder)
        except Exception:
            pass
    frame = require_frame()
    activate()
    before = count_at(count_box("favorite"))
    # The player shortcut E opens the picker (verified live); DOM/pixel follow.
    activate()
    shortcut("e")
    wait(0.9)
    if "收藏夹" not in region_text(FAV_DIALOG_BOX):
        if press_dom("collect") or press_dom("favorite"):
            wait(0.9)
    if "收藏夹" not in region_text(FAV_DIALOG_BOX):
        if not open_dialog(panel_points()["favorite"], "收藏夹", FAV_DIALOG_BOX):
            press_key("escape")
            return {"ok": False, "error": "dialog_not_open"}
    if folder:
        target = None
        for row in ocr(FAV_DIALOG_BOX):
            text = str(row.get("text", ""))
            if "新建收藏夹" in text or "个内容" in text:
                continue
            if folder in text:
                target = row
                break
        if target is None:
            press_key("escape")
            return {"ok": False, "error": "folder_not_found"}
        click(int(target["x"]) + 10, int(target["y"]) + int(target["h"]) / 2)
        wait(0.4)
    if not click_text("保存", FAV_DIALOG_BOX):
        helper("press", "--title", "保存", timeout=8)
    wait(1.3)
    after = count_at(count_box("favorite"))
    return {"ok": True, "folder": folder or "默认收藏夹", "before": before, "after": after}


def set_comments(open_comments: bool) -> dict:
    """Switch the side panel to 评论 / 简介 (CDP when available, else OCR)."""
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.comments(open_comments)
        except Exception:
            pass
    frame = require_frame()
    activate()
    tabs = (880, 225, 390, 45)
    if open_comments:
        if not click_text("评论", tabs):
            click(*frame.comments)
    else:
        click_text("简介", tabs)
    wait(0.9)
    text = region_text((880, 265, 390, 650))
    visible = "最热" in text or "发布" in text or "友善的评论" in text
    ok = visible if open_comments else not visible
    return {"ok": ok, "open": open_comments, "visible": visible}


def send_danmaku(text: str) -> dict:
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                result = cdp.danmaku(text)
            if result.get("ok"):
                return result
        except Exception:
            pass
    frame = require_frame()
    activate()
    bar = (frame.x + 150, frame.y + 545, frame.w - 170, 70)
    if not (click_text("弹幕", bar) or click_text("发送", bar)):
        click(*frame.danmaku_input)
    wait(0.7)
    type_text(text)
    wait(0.4)
    typed = region_text(DANMAKU_INPUT_BOX)
    if text[:2] not in typed:
        # The placeholder click can miss while the bar re-renders; fall back to
        # the fixed input position and type once more.
        click(*frame.danmaku_input)
        wait(0.6)
        type_text(text)
        wait(0.4)
        typed = region_text(DANMAKU_INPUT_BOX)
    press_key("return")
    wait(1.0)
    after = region_text(DANMAKU_INPUT_BOX)
    sent = typed != "" and (text[:2] in typed) and after != typed
    return {"ok": sent, "typed": typed, "after": after}


def _seek_presses(count: int, key: str) -> None:
    helper("keyseq", "--key", key, "--count", str(max(1, min(60, count))), timeout=25)


def seek_by(seconds: int) -> dict:
    """Relative seek through CDP when available, else batched arrow steps."""
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.seek_by(seconds)
        except Exception:
            pass
    frame = require_frame()
    activate()
    before = player_seconds(frame)
    if before is None:
        return {"ok": False, "error": "no_player_time"}
    target = before + int(seconds)
    current = before
    for _ in range(2):
        delta = target - current
        if abs(delta) <= SEEK_STEP_SECONDS // 2:
            break
        _seek_presses(round(abs(delta) / SEEK_STEP_SECONDS), "right" if delta > 0 else "left")
        wait(0.5)
        observed = player_seconds()
        if observed is None:
            break
        current = observed
    ok = abs(current - target) <= SEEK_STEP_SECONDS * 2
    return {"ok": ok, "before": before, "after": current, "target": target, "delta": current - before}


def seek_to(target_s: int, duration_s: int | None = None) -> dict:
    """Exact seek through CDP when available, else batched arrow steps."""
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.seek_to(target_s)
        except Exception:
            pass
    frame = require_frame()
    current = player_seconds(frame)
    if current is None:
        return {"ok": False, "error": "no_player_time", "target": target_s}
    return seek_by(target_s - current)


def next_episode() -> dict:
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                result = cdp.next_episode()
            if result.get("ok"):
                return result
        except Exception:
            pass
    frame = require_frame()
    activate()
    if not press_dom("next"):
        if not bar_visible(frame):
            return {"ok": False, "error": "bar_not_visible"}
        click(*frame.next_episode)
    wait(1.5)
    return {"ok": True, "time": player_seconds()}


def home() -> dict:
    """Close the floating player back to the home page via CDP when possible."""
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.home()
        except Exception:
            pass
    return {"ok": False, "error": "cdp_unavailable"}


def prev_episode() -> dict:
    """Previous part/episode: CDP 多P上一P button (no pixels, no network)."""
    session = cdp_session()
    if session is not None:
        try:
            with session as cdp:
                return cdp.prev_episode()
        except Exception:
            pass
    return {"ok": False, "error": "no_previous_episode"}


def read_volume() -> int | None:
    proc = _run(["osascript", "-e", "output volume of (get volume settings)"], 5)
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def set_volume(percent: int) -> dict:
    percent = max(0, min(100, int(percent)))
    before = read_volume()
    _run(["osascript", "-e", f"set volume output volume {percent}"], 5)
    wait(0.2)
    after = read_volume()
    return {"ok": after is not None and abs(after - percent) <= 2, "before": before, "after": after}
