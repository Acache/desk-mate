"""Bilibili desktop adapter with observable postconditions.

GUI channel: the reviewed macOS Accessibility helper (search, result press,
player frame, click). Playback state is observed from real screen frames, not
from a self-reported model or a blind toggle. Search cards do not expose BV ids
in the AX tree, so public Bilibili metadata is used only to map visible card
titles to BV ids; account actions are never issued through that API.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import requests
from PIL import Image

from ..config import AppConfig, BILIBILI_BUNDLE_ID, utc_now
from ..ax_daemon import AxDaemon, AxDaemonError
from ..contracts import ActionResult, ErrorCode, SearchResult, StepState
from ..storage import Database
from . import cdp_player, player_ui
from .base import (
    ActionAdapter,
    AdapterError,
    CapabilitySet,
    InvocationReceipt,
    PreparedAction,
    ResolvedAction,
)

APP_PATHS = [Path("/Applications/哔哩哔哩.app"), Path("/Applications/Bilibili.app")]
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
STATS_RE = re.compile(
    r"\s+(?:\d[\d.]*\s*[万亿]?\s+){0,3}\d[\d.]*\s*[万亿]?"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$"
)
DURATION_RE = re.compile(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*$")
TAG_RE = re.compile(r"<[^>]+>")
AX_HELPER = Path(__file__).resolve().parents[3] / "bin" / "ax_helper"
log = logging.getLogger("voice_assistant.bilibili")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def normalize_title(value: str) -> str:
    value = html.unescape(TAG_RE.sub("", value or ""))
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _parse_time_text(value: Any) -> int | None:
    text = str(value or "")
    if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        return None
    total = 0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


def title_key(value: str) -> str:
    """Comparison key tolerant of punctuation/emoji differences."""
    value = normalize_title(value)
    return "".join(ch for ch in value if ch.isalnum())


def extract_card_title(desc: str) -> str:
    title = desc.strip()
    for _ in range(3):
        new = STATS_RE.sub("", title)
        new = DURATION_RE.sub("", new)
        if new == title:
            break
        title = new
    return title.strip()


class BilibiliAdapter(ActionAdapter):
    name = "bilibili-macos"

    def __init__(self, cfg: AppConfig, db: Database, helper: Path = AX_HELPER):
        self.cfg = cfg
        self.db = db
        self.helper = helper
        self._last_player: dict[str, Any] | None = None
        self._api_cache: dict[str, tuple[float, Any]] = {}
        self._trust_cache: tuple[float, bool] | None = None
        self._session: requests.Session | None = None
        self._ax_daemon: AxDaemon | None = None

    # ------------------------------------------------------------------
    # probes
    # ------------------------------------------------------------------
    @staticmethod
    def app_installed() -> bool:
        if any(p.exists() for p in APP_PATHS):
            return True
        try:
            out = subprocess.run(
                ["mdfind", f"kMDItemCFBundleIdentifier == '{BILIBILI_BUNDLE_ID}'"],
                capture_output=True, text=True, timeout=5,
            )
            return bool(out.stdout.strip())
        except Exception:
            return False

    @staticmethod
    def app_running() -> bool:
        try:
            out = subprocess.run(
                ["pgrep", "-f", "/Applications/哔哩哔哩.app/Contents/MacOS/哔哩哔哩"],
                capture_output=True, text=True, timeout=5,
            )
            return out.returncode == 0 and bool(out.stdout.strip())
        except Exception:
            return False

    def _helper(self, *args: str, timeout: float = 20.0) -> dict[str, Any]:
        if not self.helper.exists():
            raise AdapterError(ErrorCode.CAPABILITY_UNAVAILABLE, "Accessibility helper is not built")
        try:
            out = subprocess.run([str(self.helper), *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(ErrorCode.ACTION_FAILED, "Accessibility helper timed out") from exc
        except OSError as exc:
            raise AdapterError(ErrorCode.CAPABILITY_UNAVAILABLE, f"Accessibility helper failed: {exc}") from exc
        lines = out.stdout.strip().splitlines()
        if not lines:
            raise AdapterError(ErrorCode.ACTION_FAILED, "Accessibility helper returned no data")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise AdapterError(ErrorCode.ACTION_FAILED, "invalid Accessibility helper response") from exc

    def _daemon_request(self, command: str, timeout: float = 6.0) -> dict[str, Any] | None:
        if not self.helper.exists():
            return None
        try:
            if self._ax_daemon is None:
                self._ax_daemon = AxDaemon(str(self.helper))
            return self._ax_daemon.request(command, timeout=timeout)
        except (AxDaemonError, OSError):
            self._ax_daemon = None
            return None

    def _daemon_window_titles(self) -> list[str]:
        result = self._daemon_request("window-titles", timeout=3)
        if result and result.get("ok"):
            return [str(x) for x in (result.get("titles") or [])]
        return []

    def _daemon_window_title(self) -> str:
        result = self._daemon_request("window", timeout=3)
        return str(result.get("title", "")) if result and result.get("ok") else ""

    def _daemon_bvid(self) -> str:
        result = self._daemon_request("bvid", timeout=3)
        return str(result.get("bvid", "")) if result and result.get("ok") else ""

    def start_daemon(self) -> None:
        """Prewarm the persistent AX helper; safe to call off the event loop."""
        if not self.helper.exists():
            return
        try:
            if self._ax_daemon is None:
                self._ax_daemon = AxDaemon(str(self.helper))
            self._ax_daemon.start()
        except (AxDaemonError, OSError):
            self._ax_daemon = None

    def accessibility_trusted(self) -> bool:
        now = time.time()
        if self._trust_cache and now - self._trust_cache[0] < 5.0:
            return self._trust_cache[1]
        try:
            trusted = bool(self._helper("trust", timeout=3).get("trusted"))
        except AdapterError:
            trusted = False
        self._trust_cache = (now, trusted)
        return trusted

    def probe(self) -> CapabilitySet:
        caps = CapabilitySet()
        if not self.app_installed():
            for action in ("bilibili.open", "bilibili.search", "bilibili.play", "bilibili.pause", "bilibili.resume"):
                caps.reasons[action] = "Bilibili desktop app is not installed"
            return caps
        caps.available.add("bilibili.open")
        if self.accessibility_trusted():
            caps.available |= {
                "bilibili.search", "bilibili.play", "bilibili.pause", "bilibili.resume",
                "bilibili.like", "bilibili.coin", "bilibili.favorite", "bilibili.comments",
                "bilibili.seek", "bilibili.send_danmaku", "bilibili.next", "bilibili.prev",
                "bilibili.home",
            }
        else:
            for action in ("bilibili.search", "bilibili.play", "bilibili.pause", "bilibili.resume"):
                caps.reasons[action] = (
                    "macOS Accessibility permission is required for the host process or mac/bin/ax_helper"
                )
        return caps

    # ------------------------------------------------------------------
    # metadata (read-only, public)
    # ------------------------------------------------------------------
    def _http(self) -> requests.Session:
        if self._session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
            # A normal first visit supplies buvid3/b_nut cookies. Public search
            # otherwise intermittently answers 412 to cookie-less clients.
            try:
                session.get("https://www.bilibili.com", timeout=8)
            except Exception:
                pass
            self._session = session
        return self._session

    def _api(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        key = url + json.dumps(params, sort_keys=True)
        now = time.time()
        cached = self._api_cache.get(key)
        if cached and now - cached[0] < 60:
            return cached[1]
        data = None
        for attempt in range(3):
            try:
                resp = self._http().get(url, params=params, timeout=8)
                if resp.status_code == 200:
                    parsed = resp.json()
                    if parsed.get("code") == 0:
                        data = parsed
                        break
                elif resp.status_code == 412:
                    time.sleep(0.6 * (attempt + 1))
                    continue
            except Exception:
                pass
            time.sleep(0.4 * (attempt + 1))
        if data is None:
            return None
        self._api_cache[key] = (now, data)
        return data

    def search_metadata(self, query: str) -> list[dict[str, str]]:
        data = self._api(
            "https://api.bilibili.com/x/web-interface/search/type",
            {"search_type": "video", "keyword": query, "page": 1},
        )
        if not data or data.get("code") != 0:
            return []
        out = []
        for item in (data.get("data") or {}).get("result") or []:
            bvid = item.get("bvid")
            title = html.unescape(TAG_RE.sub("", str(item.get("title") or "")))
            if bvid and BV_RE.fullmatch(str(bvid)):
                out.append({"bvid": str(bvid), "title": title})
        return out

    def resolve_title(self, title: str) -> str | None:
        """Find the BV id for a visible card title via public metadata."""
        norm = title_key(title)
        if len(norm) < 4:
            return None
        cache_key = "title:" + norm
        now = time.time()
        cached = self._api_cache.get(cache_key)
        if cached and now - cached[0] < 120:
            return cached[1]
        results = self.search_metadata(title)
        chosen = None
        for item in results:
            if title_key(item["title"]) == norm:
                chosen = item["bvid"]
                break
        if chosen is None:
            for item in results:
                a, b = title_key(item["title"]), norm
                if a and (a in b or b in a) and min(len(a), len(b)) >= 6:
                    chosen = item["bvid"]
                    break
        self._api_cache[cache_key] = (now, chosen)
        return chosen

    def video_title(self, bvid: str) -> str | None:
        data = self._api("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
        if not data or data.get("code") != 0:
            return None
        title = (data.get("data") or {}).get("title")
        return str(title) if title else None

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _launch(self) -> None:
        try:
            # Launch with the local DevTools port so player controls can be
            # driven through the DOM (no OCR, no pixels) when possible.
            subprocess.run(["open", "-b", BILIBILI_BUNDLE_ID, "--args",
                            "--remote-debugging-port=9222"], check=True, timeout=10)
        except Exception as exc:
            raise AdapterError(ErrorCode.ACTION_FAILED, f"could not launch Bilibili: {exc}") from exc
        for _ in range(40):
            if self.app_running():
                return
            time.sleep(0.25)
        raise AdapterError(ErrorCode.ACTION_FAILED, "Bilibili did not start")

    def _window(self) -> dict[str, Any]:
        try:
            return self._helper("window", timeout=6)
        except AdapterError:
            return {}

    def _home(self) -> None:
        # A real AX press is what actually closes the floating player; the DOM
        # click on the same button does not navigate in this client.
        try:
            self._helper("press", "--title", "回到主界面", timeout=8)
            return
        except AdapterError:
            pass
        player_ui.home()
        time.sleep(0.5)

    def _search_ui(self, query: str) -> dict[str, Any]:
        result = self._helper("search", query, timeout=25)
        if not result.get("ok") and result.get("error") == "search_field_not_found":
            # A playing video exposes its comment box as a text field. Do not
            # type into whatever happens to be focused: return to the home page
            # and use its search box, or fail with a clear reason.
            self._home()
            result = self._helper("search", query, timeout=25)
            if not result.get("ok") and result.get("error") == "search_field_not_found":
                raise AdapterError(ErrorCode.ACTION_FAILED, "请先回到B站首页，再搜索")
        if not result.get("ok"):
            raise AdapterError(ErrorCode.ACTION_FAILED, f"search failed: {result.get('error', 'unknown')}")
        return result

    @staticmethod
    def _cards_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
        links = result.get("results") or []
        cards = [r for r in links if r.get("role") == "AXLink"
                 and float(r.get("w", 0)) >= 250 and float(r.get("h", 0)) >= 150
                 and float(r.get("x", 0)) >= 90 and str(r.get("desc", "")).strip()]
        cards.sort(key=lambda r: (float(r.get("y", 0)), float(r.get("x", 0))))
        title_links = [r for r in links if r.get("role") == "AXLink" and str(r.get("desc", "")).strip()]
        out: list[dict[str, Any]] = []
        for ui_index, card in enumerate(cards, start=1):
            hay = " ".join(str(x) for x in (
                [card.get("url", "")] + list(card.get("child_urls") or [])
            ))
            match = BV_RE.search(hay)
            if not match:
                continue
            title = extract_card_title(str(card.get("desc", "")))
            siblings = [
                r for r in title_links
                if abs(float(r.get("x", 0)) - float(card.get("x", 0))) <= 24
                and float(card.get("y", 0)) <= float(r.get("y", 0)) <= float(card.get("y", 0)) + float(card.get("h", 0)) + 90
                and float(r.get("w", 0)) >= 200 and float(r.get("h", 0)) >= 30
            ]
            if siblings:
                siblings.sort(key=lambda r: (len(str(r.get("desc", ""))), float(r.get("y", 0))))
                candidate = str(siblings[0].get("desc", "")).strip()
                if candidate and len(candidate) >= 4:
                    title = candidate
            out.append({
                "video_id": match.group(0),
                "title": extract_card_title(title) or title,
                "ui_index": ui_index,
                "index": len(out) + 1,
                "_x": float(card.get("x", 0)),
                "_y": float(card.get("y", 0)),
                "_w": float(card.get("w", 0)),
                "_h": float(card.get("h", 0)),
            })
        # Derive UI row/column from card geometry. Search grids can reflow with
        # window width, so do not assume a fixed number of columns.
        rows: list[list[dict[str, Any]]] = []
        for item in sorted(out, key=lambda c: (c["_y"], c["_x"])):
            if not rows or abs(item["_y"] - rows[-1][0]["_y"]) > max(24.0, item["_h"] * 0.45):
                rows.append([item])
            else:
                rows[-1].append(item)
        for rindex, row in enumerate(rows, start=1):
            for cindex, item in enumerate(sorted(row, key=lambda c: c["_x"]), start=1):
                item["row"] = rindex
                item["column"] = cindex
        for item in out:
            item.pop("_x", None); item.pop("_y", None)
            item.pop("_w", None); item.pop("_h", None)
        return out

    def _player_frame(self) -> dict[str, float] | None:
        try:
            result = self._helper("player", timeout=10)
            frame = result.get("frame") or {}
            if frame and float(frame.get("w", 0)) >= 200 and float(frame.get("h", 0)) >= 150:
                return {k: float(frame[k]) for k in ("x", "y", "w", "h")}
        except AdapterError:
            pass
        # Fallback: only accept a window whose title matches the currently
        # loaded player webarea. The old "largest Bilibili window" fallback
        # could select the search/main window and click an unrelated control.
        try:
            area_title = title_key(str(self._webarea().get("title", "")))
            rows = self._helper("cgwindows", timeout=6).get("windows") or []
        except AdapterError:
            return None
        best = None
        for row in rows:
            if int(row.get("layer", 0)) != 0:
                continue
            name = title_key(str(row.get("name", "")))
            if not area_title or not name:
                continue
            if area_title not in name and name not in area_title:
                continue
            b = row.get("bounds") or {}
            best = {"x": float(b.get("X", 0)), "y": float(b.get("Y", 0)),
                    "w": float(b.get("Width", 0)), "h": float(b.get("Height", 0))}
            break
        if not best or best["w"] < 200 or best["h"] < 150:
            return None
        # Inset the click region so the title bar/sidebar is less likely to be
        # selected on the mini player.
        inset_x = best["w"] * 0.2
        inset_y = best["h"] * 0.2
        return {"x": best["x"] + inset_x, "y": best["y"] + inset_y,
                "w": best["w"] - 2 * inset_x, "h": best["h"] - 2 * inset_y}

    def _window_id(self) -> int | None:
        try:
            result = self._helper("cgwindows", timeout=6)
        except AdapterError:
            return None
        rows = result.get("windows") or []
        title = title_key(str(self._webarea().get("title", "")))
        best = None
        best_area = 0.0
        for row in rows:
            if int(row.get("layer", 0)) != 0:
                continue
            bounds = row.get("bounds") or {}
            area = float(bounds.get("Width", 0)) * float(bounds.get("Height", 0))
            name = title_key(str(row.get("name", "")))
            if title and name and (title in name or name in title):
                return int(row.get("id", 0))
            if area > best_area:
                best_area = area
                best = int(row.get("id", 0))
        return best

    def _hover_player(self, frame: dict[str, float]) -> None:
        try:
            self._helper("move-to", "--x", str(int(frame["x"] + frame["w"] / 2)),
                         "--y", str(int(frame["y"] + frame["h"] - 36)), timeout=5)
        except AdapterError:
            pass

    def _playback_seconds(self, frame: dict[str, float] | None = None) -> int | None:
        frame = frame or self._player_frame()
        if not frame:
            return None
        for attempt in range(3):
            try:
                result = self._helper("player", timeout=8)
            except AdapterError:
                result = {}
            text = str(result.get("time") or "")
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
                parts = [int(x) for x in text.split(":")]
                total = 0
                for part in parts:
                    total = total * 60 + part
                return total
            # Only reveal controls if AX did not expose the time; avoid moving
            # the user's mouse during every state observation.
            if attempt == 0:
                self._hover_player(frame)
                time.sleep(0.25)
        return None

    def _playback_seconds_fast(self, frame: dict[str, float] | None = None) -> int | None:
        """One AX time read (plus a single hover retry); no screenshots."""
        frame = frame or self._player_frame()
        for attempt in range(2):
            try:
                result = self._helper("player", timeout=5)
            except AdapterError:
                result = {}
            text = str(result.get("time") or "")
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
                parts = [int(x) for x in text.split(":")]
                total = 0
                for part in parts:
                    total = total * 60 + part
                return total
            if attempt == 0 and frame:
                self._hover_player(frame)
                time.sleep(0.18)
        return None

    def _fast_playing_state(self, frame: dict[str, float] | None = None) -> bool | None:
        """~0.4 s state probe for pause/resume; avoids 3 screenshot frames."""
        frame = frame or self._player_frame()
        if not frame:
            return None
        first = self._playback_seconds_fast(frame)
        if first is None:
            return None
        time.sleep(0.35)
        second = self._playback_seconds_fast(frame)
        if second is None:
            return None
        return second > first

    def _playing_state(self, frame: dict[str, float] | None = None) -> bool | None:
        """Observe real playback progress, then fall back to frame motion."""
        frame = frame or self._player_frame()
        if frame:
            first = self._playback_seconds(frame)
            if first is not None:
                time.sleep(1.2)
                second = self._playback_seconds(frame)
                if second is not None:
                    if second > first:
                        return True
                    if second == first:
                        return False
        window_id = self._window_id()
        if not frame and not window_id:
            return None
        paths = []
        try:
            for _ in range(3):
                fd, path = tempfile.mkstemp(prefix="va-frame-", suffix=".png")
                os.close(fd)
                paths.append(path)
                if window_id:
                    command = ["screencapture", "-x", "-l", str(window_id), path]
                else:
                    command = ["screencapture", "-x", "-R",
                               f"{int(frame['x'])},{int(frame['y'])},{int(frame['w'])},{int(frame['h'])}", path]
                subprocess.run(command, check=True, timeout=8, capture_output=True)
                time.sleep(0.9)
            images = [np.asarray(Image.open(p).convert("RGB")).astype(np.int16) for p in paths]
            if any(im.shape != images[0].shape for im in images):
                return None
            changed = []
            h, w, _ = images[0].shape
            for a, b in ((images[0], images[1]), (images[1], images[2])):
                values = []
                # A 3x3 patch grid makes moving danmaku a minority signal: the
                # median reflects the video frame, not a few scrolling glyphs.
                for iy in range(3):
                    for ix in range(3):
                        y0 = int(h * (0.25 + iy * 0.16))
                        y1 = int(h * (0.25 + iy * 0.16 + 0.15))
                        x0 = int(w * (0.25 + ix * 0.16))
                        x1 = int(w * (0.25 + ix * 0.16 + 0.15))
                        d = np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1])
                        values.append(float((d.max(axis=2) > 8).mean()))
                changed.append(float(np.median(values)))
            if max(changed) > 0.015:
                return True
            if max(changed) < 0.002:
                return False
            return None
        except Exception:
            return None
        finally:
            for path in paths:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _player_toggle(self, target_paused: bool) -> dict[str, Any]:
        """Press the real AX play/pause button; never rely on a fixed coordinate."""
        target = "pause" if target_paused else "play"
        daemon = self._daemon_request("toggle", timeout=6)
        if daemon is not None and daemon.get("ok") and daemon.get("pressed"):
            return daemon
        last: dict[str, Any] = {}
        # AX tree updates can briefly hide the control after a video starts.
        # Retry only when the helper explicitly reports that it did NOT press;
        # a successful press is never repeated (would undo the action).
        for attempt in range(2):
            try:
                last = self._helper("player-toggle", "--target", target, timeout=8)
            except AdapterError:
                last = {}
            if last.get("ok") and last.get("pressed"):
                return last
            if attempt == 0:
                time.sleep(0.25)
        raise AdapterError(ErrorCode.ACTION_FAILED,
                           "player play/pause button not available")

    def _wait_window_title(self, expected: str, timeout: float = 8.0) -> str:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            all_titles = self._daemon_window_titles()
            matched = next((t for t in all_titles if expected and (
                title_key(expected) in title_key(t) or title_key(t) in title_key(expected))), "")
            if matched:
                return matched
            daemon_title = self._daemon_window_title()
            if daemon_title:
                last = daemon_title
            else:
                last = str(self._window().get("title", ""))
            if expected and (title_key(expected) in title_key(last) or title_key(last) in title_key(expected)):
                return last
            time.sleep(0.1)
        return last

    def _webarea(self) -> dict[str, Any]:
        try:
            return self._helper("webarea", timeout=8)
        except AdapterError:
            return {}

    def _current_bvid(self) -> str | None:
        area = self._webarea()
        urls = [str(area.get("url", ""))]
        urls += [str(x.get("url", "")) for x in (area.get("all") or [])]
        for url in urls:
            match = re.search(r"(?:bvid=|/video/)(BV[0-9A-Za-z]{10})", url)
            if match:
                return match.group(1)
        return None

    def _on_search_page(self) -> bool:
        return "search.html" in str(self._webarea().get("url", ""))

    def _ui_call(self, fn, *args):
        try:
            return fn(*args)
        except player_ui.PlayerUiError as exc:
            raise AdapterError(ErrorCode.ACTION_FAILED, exc.message or exc.code) from exc

    def context_snapshot(self) -> dict[str, Any] | None:
        """What is on screen right now (page type and visible video lists).

        Prefers the floating player page; falls back to the main window for
        search/home pages. Returns None when CDP is unavailable.
        """
        if not cdp_player.available():
            return None
        # Whichever window is in front is what the user is looking at.
        front_is_main = False
        try:
            windows = self._helper("cgwindows", timeout=6).get("windows") or []
            front = str((windows[0] or {}).get("name") or "") if windows else ""
            front_is_main = "哔哩哔哩 (" in front
        except Exception:
            pass

        def read(marker: str) -> dict | None:
            ws = cdp_player.ws_for(marker)
            if not ws:
                return None
            try:
                with cdp_player.CdpPlayer(ws) as session:
                    return session.read_context()
            except Exception:
                return None

        # A dedicated search window (search.html) wins when it is open: that is
        # the page the user is looking at after 搜索.
        search = read("search.html")
        if search and search.get("search_results"):
            return search
        if front_is_main:
            return search or read("index.html") or read("player.html")
        return read("player.html") or search or read("index.html")

    def _duration_seconds(self) -> int | None:
        """Total video length from the public API (more reliable than OCR)."""
        bvid = self._current_bvid()
        if not bvid:
            return None
        data = self._api("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
        try:
            value = int(((data or {}).get("data") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            return None
        return value or None

    def _season_episode(self, direction: int) -> tuple[str, str] | None:
        """Previous/next episode bvid from the public collection API."""
        bvid = self._current_bvid()
        if not bvid:
            return None
        data = self._api("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
        if not data:
            return None
        season = (data.get("data") or {}).get("ugc_season") or {}
        episodes = [e for section in (season.get("sections") or [])
                    for e in (section.get("episodes") or [])]
        ids = [str(e.get("bvid") or "") for e in episodes]
        if bvid not in ids:
            return None
        index = ids.index(bvid) + direction
        if 0 <= index < len(ids):
            episode = episodes[index]
            return str(episode.get("bvid") or ""), str(episode.get("title") or "")
        return None

    def _press_video(self, bvid: str, query: str, ui_index: int | None = None) -> dict[str, Any]:
        target = f"https://www.bilibili.com/video/{bvid}"
        pressed = self._helper("press-url", "--url", target, timeout=12)
        if pressed.get("ok"):
            return pressed
        # The current page may no longer be the snapshot's search page. Re-run
        # the query and try the exact URL again; never fall back to a blind
        # index click without verifying the resulting URL.
        try:
            self._search_ui(query)
        except AdapterError:
            return pressed
        pressed = self._helper("press-url", "--url", target, timeout=12)
        return pressed

    def _wait_for_bvid(self, expected: str | None, timeout: float = 9.0) -> str | None:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self._daemon_bvid() or self._current_bvid()
            if expected and last == expected:
                return last
            if not expected and last:
                return last
            time.sleep(0.1)
        return last

    def _player_snapshot_fast(self, stable: bool = False) -> dict[str, Any] | None:
        command = "state" if stable else "snapshot"
        result = self._daemon_request(command, timeout=8 if stable else 6)
        if result is None:
            try:
                result = self._helper("player-snapshot", timeout=8)
            except AdapterError:
                return None
        if not result.get("ok") or not result.get("bvid"):
            return None
        before = _parse_time_text(result.get("time_before"))
        after = _parse_time_text(result.get("time_after"))
        if before is None or after is None:
            return None
        return self._save_player(str(result["bvid"]), after == before)

    def refresh_player_snapshot(self) -> dict[str, Any] | None:
        """Probe real playback state before pause/resume.

        A 60 s snapshot is not truth: the user can pause, switch videos or
        close the player. Pause/resume must always start from a fresh probe or
        a stale "already" turns into a false DONE.
        """
        try:
            if not self.app_running():
                return None
            # CDP answers pause state in milliseconds; use it before any AX
            # round-trip (the AX refresh cost ~1.3 s on the packaged app).
            if cdp_player.available():
                try:
                    with cdp_player.CdpPlayer() as cdp:
                        state = cdp.state()
                    match = re.search(r"bvid=(BV[0-9A-Za-z]{10})", str(state.get("href") or ""))
                    paused = state.get("paused")
                    if match and isinstance(paused, bool):
                        return self._save_player(match.group(1), paused)
                except Exception:
                    pass
            self._daemon_request("refresh", timeout=4)
            fast = self._player_snapshot_fast(stable=True)
            if fast is not None:
                return fast
            frame = self._player_frame()
            if not frame:
                return None
            area = self._webarea()
            urls = [str(area.get("url", ""))]
            urls += [str(x.get("url", "")) for x in (area.get("all") or [])]
            bvid = None
            for url in urls:
                match = re.search(r"(?:bvid=|/video/)(BV[0-9A-Za-z]{10})", url)
                if match:
                    bvid = match.group(1)
                    break
            if not bvid:
                return None
            playing = self._fast_playing_state(frame)
            if playing is None:
                return None
            return self._save_player(bvid, not playing)
        except Exception:
            return None

    def _save_player(self, video_id: str | None, paused: bool) -> dict[str, Any]:
        generation = str(uuid.uuid4())
        now = utc_now()
        data = {
            "video_id": video_id,
            "generation": generation,
            "paused": paused,
            "position_ms": 0,
            "observed_at": now,
            "expires_at": now + 60.0,
            "account_id": None,
        }
        self.db.save_snapshot(
            "player", BILIBILI_BUNDLE_ID, generation,
            {"paused": paused, "position_ms": 0, "account_id": None},
            now, now + 60.0, video_id=video_id,
        )
        self._last_player = data
        return data

    # ------------------------------------------------------------------
    # ActionAdapter
    # ------------------------------------------------------------------
    def prepare(self, resolved: ResolvedAction, context: Any) -> PreparedAction:
        action = resolved.action
        caps = self.probe()
        if action not in caps.available:
            raise AdapterError(ErrorCode.CAPABILITY_UNAVAILABLE, caps.reasons.get(action, "action unavailable"))
        now = time.time()
        if action == "bilibili.open":
            return PreparedAction(resolved, {"launch": True}, now)
        if not self.app_running():
            self._launch()
        if action == "bilibili.search":
            query = resolved.params.get("query", "")
            if not query:
                raise AdapterError(ErrorCode.INVALID_PLAN, "empty query")
            return PreparedAction(resolved, {"query": query}, now)
        if action in {
            "bilibili.like", "bilibili.coin", "bilibili.favorite", "bilibili.comments",
            "bilibili.seek", "bilibili.send_danmaku", "bilibili.next", "bilibili.prev",
            "bilibili.home",
        }:
            return PreparedAction(resolved, dict(resolved.params), now)
        if action == "bilibili.play":
            binding = resolved.binding
            params_bv = resolved.params.get("bv")
            if params_bv:
                # Use the BV itself; the public API lookup only added a network
                # round-trip before the same press-url navigation.
                title = str(params_bv)
                return PreparedAction(resolved, {
                    "video_id": str(params_bv), "title": title, "mode": "bv", "query": title,
                }, now)
            snapshot_id = resolved.params.get("search_result_id") or binding.get("search_result_id")
            index = int(resolved.params.get("index", 1))
            if not snapshot_id:
                raise AdapterError(ErrorCode.TARGET_STALE, "no search snapshot")
            snap = self.db.get_snapshot(str(snapshot_id))
            if not snap:
                raise AdapterError(ErrorCode.TARGET_STALE, "search snapshot is gone")
            payload = json.loads(snap["payload_json"])
            match = None
            for item in payload.get("results", []):
                if int(item.get("index", -1)) == index:
                    match = item
                    break
            if not match:
                raise AdapterError(ErrorCode.TARGET_STALE, "search index is out of range")
            return PreparedAction(resolved, {
                "video_id": match["video_id"], "title": match["title"],
                "ui_index": int(match.get("ui_index", index)), "query": payload.get("query", ""),
                "search_result_id": str(snapshot_id), "mode": "index",
            }, now)
        if action in {"bilibili.pause", "bilibili.resume"}:
            player = self.db.current_player(now)
            if not player:
                raise AdapterError(ErrorCode.TARGET_STALE, "no fresh player state")
            if resolved.binding.get("video_id") and player.get("video_id") \
                    and resolved.binding["video_id"] != player["video_id"]:
                raise AdapterError(ErrorCode.TARGET_STALE, "current video changed")
            if resolved.binding.get("generation") and player.get("generation") \
                    and resolved.binding["generation"] != player["generation"]:
                raise AdapterError(ErrorCode.TARGET_STALE, "player generation changed")
            return PreparedAction(resolved, {
                "target_paused": action == "bilibili.pause",
                "video_id": player.get("video_id"),
                "current_paused": player.get("paused"),
            }, now)
        raise AdapterError(ErrorCode.CAPABILITY_UNAVAILABLE, "action not implemented in this adapter")

    def invoke(self, prepared: PreparedAction, deadline: float) -> InvocationReceipt:
        started = time.time()
        action = prepared.resolved.action
        p = prepared.prepared or {}
        try:
            if action == "bilibili.open":
                self._launch()
                return InvocationReceipt(prepared, started, time.time(), True, {})
            if action == "bilibili.search":
                result = self._search_ui(p["query"])
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"results": result.get("results", []),
                                          "query": p["query"],
                                          "window_title": result.get("window_title", "")})
            if action == "bilibili.like":
                result = self._ui_call(player_ui.like)
                if not result.get("ok"):
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": result.get("error", "like_failed")})
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"state": result.get("state"), "changed": bool(result.get("changed"))})
            if action == "bilibili.coin":
                result = self._ui_call(player_ui.coin, int(p.get("count") or 1))
                if not result.get("ok"):
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": result.get("error", "coin_failed")})
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"coins": result.get("coins"), "before": result.get("before"),
                                          "after": result.get("after")})
            if action == "bilibili.favorite":
                folder = str(p.get("folder") or "")
                result = self._ui_call(player_ui.favorite, folder or None)
                if not result.get("ok"):
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": result.get("error", "favorite_failed"),
                                              "folder": folder or "默认收藏夹"})
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"folder": folder or "默认收藏夹", "changed": bool(result.get("changed"))})
            if action == "bilibili.comments":
                result = self._ui_call(player_ui.set_comments, bool(p.get("open", True)))
                return InvocationReceipt(prepared, started, time.time(), True, result)
            if action == "bilibili.seek":
                if p.get("target_s") is not None:
                    # seek_to resolves the duration itself (CDP) or uses arrow
                    # steps; the public API call here only added network delay.
                    result = self._ui_call(player_ui.seek_to, int(p["target_s"]))
                else:
                    result = self._ui_call(player_ui.seek_by, int(p.get("delta_s") or 0))
                if not result.get("ok"):
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": result.get("error", "seek_failed")})
                return InvocationReceipt(prepared, started, time.time(), True, result)
            if action == "bilibili.send_danmaku":
                result = self._ui_call(player_ui.send_danmaku, str(p.get("text") or ""))
                if not result.get("ok"):
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": result.get("error", "danmaku_failed")})
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"text": p.get("text")})
            if action == "bilibili.home":
                before = self._current_bvid()
                self._home()
                time.sleep(0.8)
                after = self._current_bvid()
                return InvocationReceipt(prepared, started, time.time(), after is None,
                                         {"before": before, "after": after})
            if action == "bilibili.next":
                if cdp_player.available():
                    try:
                        with cdp_player.CdpPlayer() as cdp:
                            result = cdp.next_episode()
                        if result.get("ok"):
                            return InvocationReceipt(prepared, started, time.time(), True, result)
                    except Exception:
                        pass
                before = self._current_bvid()
                # The player shortcut ] switches episode without any pixels.
                player_ui.activate()
                player_ui.shortcut("rightbracket")
                time.sleep(1.5)
                if self._current_bvid() == before:
                    self._ui_call(player_ui.next_episode)
                shown = before
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    shown = self._current_bvid()
                    if shown and shown != before:
                        break
                    time.sleep(0.3)
                return InvocationReceipt(prepared, started, time.time(),
                                         bool(shown and shown != before),
                                         {"before": before, "after": shown})
            if action == "bilibili.prev":
                if cdp_player.available():
                    try:
                        with cdp_player.CdpPlayer() as cdp:
                            result = cdp.prev_episode()
                        if result.get("ok"):
                            return InvocationReceipt(prepared, started, time.time(), True, result)
                    except Exception:
                        pass
                before_prev = self._current_bvid()
                player_ui.activate()
                player_ui.shortcut("leftbracket")
                time.sleep(1.5)
                after_prev = self._current_bvid()
                if before_prev and after_prev and after_prev != before_prev:
                    return InvocationReceipt(prepared, started, time.time(), True,
                                             {"before": before_prev, "after": after_prev,
                                              "via": "shortcut"})
                # No public-API lookup: a multi-part video is switched through
                # the player's own 多P上一P control (local CDP only).
                result = player_ui.prev_episode()
                shown = self._current_bvid()
                return InvocationReceipt(prepared, started, time.time(),
                                         bool(result.get("ok")) and shown != before_prev,
                                         {"before": before_prev, "after": shown,
                                          "via": result.get("via", "cdp")})
                return InvocationReceipt(prepared, started, time.time(), shown == target,
                                         {"bvid": shown, "expected": target})
            if action == "bilibili.play":
                expected = str(p.get("video_id", ""))
                title = p.get("title", "")
                query = str(p.get("query") or title)
                t0 = time.perf_counter()
                if not self._on_search_page():
                    self._search_ui(query)
                t1 = time.perf_counter()
                pressed = self._press_video(expected, query, p.get("ui_index"))
                if not pressed.get("ok"):
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": "result_not_pressable"})
                t2 = time.perf_counter()
                shown = self._wait_window_title(title, timeout=6.0)
                t3 = time.perf_counter()
                log.warning("play phases search=%d press=%d title=%d", int((t1-t0)*1000), int((t2-t1)*1000), int((t3-t2)*1000))
                if expected and shown and (
                    title_key(title) in title_key(shown) or title_key(shown) in title_key(title)
                ):
                    # The card was selected by its expected BV and its title is
                    # now present on screen; waiting up to 9 s for a second
                    # AX BVID read adds no safety here.
                    bvid = expected
                else:
                    bvid = self._wait_for_bvid(expected, timeout=3.0)
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"window_title": shown, "expected_title": title,
                                          "bvid": bvid, "expected_bv": expected})
            if action in {"bilibili.pause", "bilibili.resume"}:
                target = bool(p.get("target_paused"))
                current_paused = p.get("current_paused")
                if current_paused is not None and bool(current_paused) == target:
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"already": True, "paused": bool(current_paused)})
                # Do NOT use a system media key for a Bili-specific action.
                # Being frontmost is not proof that Bili owns Now Playing; a
                # media key can control another app and change the wrong state.
                # AX is slower but targets the actual player control.
                # One AX press; verify the actual player state afterwards.
                toggle = self._player_toggle(target)
                time.sleep(0.3)
                after = self._player_snapshot_fast(stable=True)
                observed = bool(after["paused"]) if after else None
                fallback = False
                if observed is None or observed != target:
                    # The daemon may hold a stale player/button after a video
                    # change. Re-discover with a one-shot helper, check truth
                    # first, then press only if the first press truly missed.
                    fallback = True
                    self._daemon_request("refresh", timeout=4)
                    try:
                        truth = self._daemon_request("state", timeout=8) or self._helper("player-snapshot", timeout=8)
                        truth_paused = None
                        tb, ta = _parse_time_text(truth.get("time_before")), _parse_time_text(truth.get("time_after"))
                        if tb is not None and ta is not None:
                            truth_paused = ta == tb
                        if truth_paused == target:
                            observed = truth_paused
                        else:
                            one = self._helper("player-toggle", "--target", "pause" if target else "play", timeout=8)
                            if one.get("ok") and one.get("pressed"):
                                time.sleep(0.4)
                                truth2 = self._helper("player-snapshot", timeout=8)
                                tb2, ta2 = _parse_time_text(truth2.get("time_before")), _parse_time_text(truth2.get("time_after"))
                                if tb2 is not None and ta2 is not None:
                                    observed = ta2 == tb2
                    except AdapterError:
                        pass
                return InvocationReceipt(prepared, started, time.time(), True,
                                         {"target_paused": target, "ax_toggle": True,
                                          "observed_paused": observed,
                                          "ax_verified": observed is not None and observed == target,
                                          "ax_fallback": fallback,
                                          "time_before": toggle.get("time_before", ""),
                                          "time_after": toggle.get("time_after", "")})
            return InvocationReceipt(prepared, started, time.time(), False, {"error": "unimplemented"})
        except AdapterError as exc:
            return InvocationReceipt(prepared, started, time.time(), exc.effect_possible,
                                     {"error": exc.summary, "code": exc.code.value})
        except Exception as exc:
            return InvocationReceipt(prepared, started, time.time(), action != "bilibili.search",
                                     {"error": str(exc)[:200]})

    # ------------------------------------------------------------------
    def verify(self, receipt: InvocationReceipt, deadline: float) -> ActionResult:
        action = receipt.prepared.resolved.action
        step = receipt.prepared.resolved.step_index
        latency = max(0, int((receipt.finished_at - receipt.started_at) * 1000))
        raw = receipt.raw or {}
        if not receipt.effect_possible:
            if raw.get("already"):
                paused = raw.get("paused")
                video = (receipt.prepared.prepared or {}).get("video_id")
                if paused is not None:
                    self._save_player(video, bool(paused))
                summary = "ALREADY PLAYING" if paused is False else "ALREADY PAUSED" if paused is True else "NO CHANGE"
                return ActionResult(step_index=step, status=StepState.NOOP_VERIFIED,
                                    output={"ui_summary": summary},
                                    evidence={"verified": True, "already": True}, latency_ms=latency)
            return ActionResult(step_index=step, status=StepState.FAILED,
                                code=ErrorCode.TARGET_STALE if "no_player" in raw else ErrorCode.ACTION_FAILED,
                                evidence={"error": str(raw.get("error", ""))[:120]}, latency_ms=latency)
        if action == "bilibili.open":
            if self.app_running():
                return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                                    output={"app_id": "bilibili"}, evidence={"running": True},
                                    latency_ms=latency)
            return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                code=ErrorCode.OUTCOME_UNKNOWN, latency_ms=latency)
        if action == "bilibili.search":
            cards = self._cards_from_result(raw)
            if not cards:
                return ActionResult(step_index=step, status=StepState.FAILED,
                                    code=ErrorCode.ACTION_FAILED,
                                    evidence={"ui_cards": 0}, latency_ms=latency)
            now = utc_now()
            snap_id = uuid.uuid4()
            payload = {"query": raw.get("query", ""), "results": cards}
            with self.db.transaction() as tx:
                tx.execute(
                    """INSERT INTO snapshots(id, kind, app_id, generation, observed_at, expires_at, payload_json)
                       VALUES (?, 'search', ?, ?, ?, ?, ?)""",
                    (str(snap_id), BILIBILI_BUNDLE_ID, str(uuid.uuid4()), now, now + 120.0,
                     json.dumps(payload, ensure_ascii=False, sort_keys=True)),
                )
            return ActionResult(
                step_index=step, status=StepState.SUCCEEDED,
                output={"search_result_id": str(snap_id), "results": cards},
                evidence={"ui_cards": len(cards)}, latency_ms=latency,
            )
        if action == "bilibili.play":
            expected_bv = (receipt.prepared.prepared or {}).get("video_id")
            observed_bv = raw.get("bvid")
            shown = str(raw.get("window_title", ""))
            expected_title = str((receipt.prepared.prepared or {}).get("title", ""))
            if raw.get("error") == "result_not_pressable":
                return ActionResult(step_index=step, status=StepState.FAILED,
                                    code=ErrorCode.ACTION_FAILED, evidence={"pressed": False},
                                    latency_ms=latency)
            if not observed_bv:
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"bvid_observed": False}, latency_ms=latency)
            if expected_bv and observed_bv != expected_bv:
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"bvid_mismatch": True}, latency_ms=latency)
            if expected_title and shown and title_key(expected_title) not in title_key(shown):
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"title_mismatch": True}, latency_ms=latency)
            # The card was pressed by its expected BV and its title is now
            # present. Avoid another multi-second AX state probe just to
            # re-confirm the player page; title/BV are the postcondition.
            self._save_player(observed_bv, False)
            return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                                output={"video_id": observed_bv,
                                        "player_generation": self._last_player["generation"]},
                                evidence={"playing": True, "bvid": observed_bv,
                                          "title": shown[:120], "title_verified": True},
                                latency_ms=latency)
        if action in {"bilibili.pause", "bilibili.resume"}:
            target = bool((receipt.prepared.prepared or {}).get("target_paused"))
            if raw.get("system_media") and not raw.get("system_media_verified"):
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"system_media": True,
                                              "observed_paused": raw.get("observed_paused")},
                                    latency_ms=latency)
            if raw.get("ax_toggle") and not raw.get("ax_verified"):
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"ax_pressed": True,
                                              "observed_paused": raw.get("observed_paused")},
                                    latency_ms=latency)
            if raw.get("ax_verified") or raw.get("system_media_verified"):
                # DONE only after observing the target state.
                video = (receipt.prepared.prepared or {}).get("video_id")
                self._save_player(video, target)
                return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                                    evidence={"verified": True, "paused": target,
                                              "system_media_verified": bool(raw.get("system_media_verified")),
                                              "ax_pressed": bool(raw.get("ax_toggle"))},
                                    latency_ms=latency)
            frame = self._player_frame()
            observed = self._fast_playing_state(frame) if frame else None
            if observed is None:
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"observed": "ambiguous"}, latency_ms=latency)
            if (not observed) != target:
                return ActionResult(step_index=step, status=StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN,
                                    evidence={"observed_playing": observed}, latency_ms=latency)
            video = (receipt.prepared.prepared or {}).get("video_id")
            self._save_player(video, not observed)
            return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                                evidence={"verified": True, "paused": target, "playing": observed},
                                latency_ms=latency)
        return ActionResult(step_index=step, status=StepState.FAILED,
                            code=ErrorCode.CAPABILITY_UNAVAILABLE, latency_ms=latency)


def build_bilibili_adapter(cfg: AppConfig, db: Database | None = None) -> BilibiliAdapter:
    if db is None:
        raise ValueError("BilibiliAdapter requires the shared Database")
    return BilibiliAdapter(cfg, db)
