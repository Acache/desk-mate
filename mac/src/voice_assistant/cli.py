"""Local CLI: doctor, pair, run, status, backup, revoke, probe-bilibili."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import AppConfig, data_root, load_config, unix_to_iso, utc_now
from .contracts import CAPABILITY_VERSION, R1_ACTIONS
from .storage import Database


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _ctx(args) -> tuple[AppConfig, Database]:
    if getattr(args, "data_dir", None):
        os.environ["VOICE_ASSISTANT_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    cfg = load_config(create=True)
    db = Database(cfg.db_path)
    return cfg, db


def cmd_setup(args) -> int:
    cfg, db = _ctx(args)
    _print({
        "data_root": str(cfg.root),
        "server": cfg.server.summary() if cfg.server else None,
        "port": cfg.port,
        "capabilities": list(R1_ACTIONS),
        "capability_version": CAPABILITY_VERSION,
    })
    db.close()
    return 0


def cmd_doctor(args) -> int:
    cfg, db = _ctx(args)
    checks: dict[str, object] = {}
    checks["macos"] = sys.platform == "darwin"
    checks["python"] = sys.version.split()[0]
    checks["data_root"] = str(cfg.root)
    checks["server_identity"] = cfg.server.summary() if cfg.server else None
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", cfg.port))
        s.close()
        checks["port_free"] = True
    except OSError:
        checks["port_free"] = False
    checks["bilibili_installed"] = Path("/Applications/哔哩哔哩.app").exists() or Path("/Applications/Bilibili.app").exists()
    helper = Path(__file__).resolve().parents[2] / "bin" / "ax_helper"
    checks["ax_helper"] = str(helper)
    if helper.exists():
        try:
            out = subprocess.run([str(helper), "trust"], capture_output=True, text=True, timeout=5)
            checks["accessibility"] = bool(json.loads(out.stdout.strip().splitlines()[-1]).get("trusted"))
        except Exception:
            checks["accessibility"] = False
    else:
        checks["accessibility"] = False
    checks["screen_capture"] = True
    try:
        from .stt import build_stt
        checks["stt"] = build_stt(cfg.stt_backend).health()
    except Exception as exc:
        checks["stt"] = {"available": False, "error": str(exc)}
    try:
        from .decision import OllamaDecision
        checks["decision"] = OllamaDecision(cfg.ollama_model, cfg.ollama_url).health()
    except Exception as exc:
        checks["decision"] = {"available": False, "error": str(exc)}
    checks["devices"] = len(db.list_devices())
    checks["active_command"] = (db.active_command() or {}).get("id")
    checks["tmp_writable"] = os.access(tempfile.gettempdir(), os.W_OK)
    checks["disk"] = shutil.disk_usage(cfg.root).free
    _print(checks)
    ready = (
        checks["macos"] and checks["port_free"] and checks.get("server_identity") is not None
        and (checks.get("stt") or {}).get("available", False)
        and (checks.get("decision") or {}).get("available", False)
    )
    db.close()
    return 0 if ready else 1


def cmd_pair(args) -> int:
    from .provisioning import choose_port, pair_device
    cfg, db = _ctx(args)
    port = choose_port(args.port)
    summary = pair_device(
        cfg, db, port, args.ssid, args.password, args.manual_host, args.name,
    )
    _print({"paired": True, "port": port, **summary})
    db.close()
    return 0


def cmd_run(args) -> int:
    from .api import run_server
    cfg, db = _ctx(args)
    db.close()
    if args.port:
        cfg.port = args.port
    run_server(cfg, ble_address=args.ble_address)
    return 0


def cmd_status(args) -> int:
    cfg, db = _ctx(args)
    rows = []
    with db._lock:
        cur = db._conn.execute(
            "SELECT id, device_id, state, created_at, updated_at, summary, error_code FROM commands "
            "ORDER BY created_at DESC LIMIT ?", (args.limit,)
        )
        for row in cur.fetchall():
            rows.append({
                "id": row["id"], "device_id": row["device_id"], "state": row["state"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "summary": row["summary"], "error_code": row["error_code"],
            })
    _print({
        "server_id": cfg.server.server_id if cfg.server else None,
        "devices": db.list_devices(),
        "commands": rows,
        "active_command": (db.active_command() or {}).get("id"),
    })
    db.close()
    return 0


def cmd_devices(args) -> int:
    cfg, db = _ctx(args)
    _print({"devices": db.list_devices()})
    db.close()
    return 0


def cmd_revoke(args) -> int:
    cfg, db = _ctx(args)
    with db._lock:
        row = db._conn.execute("SELECT 1 FROM devices WHERE id=?", (args.device_id,)).fetchone()
    if not row:
        _print({"revoked": False, "reason": "device not found"})
        db.close()
        return 1
    db.revoke_device(args.device_id)
    _print({"revoked": True, "device_id": args.device_id})
    db.close()
    return 0


def cmd_backup(args) -> int:
    cfg, db = _ctx(args)
    dest = Path(args.output).expanduser() if args.output else cfg.root / "backups" / f"learning-{int(utc_now())}.db"
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with db._lock:
            backup = sqlite3.connect(str(dest))
            db._conn.backup(backup)
            backup.close()
    finally:
        db.close()
    os.chmod(dest, 0o600)
    _print({"backup": str(dest), "size": dest.stat().st_size})
    return 0


def cmd_probe_bilibili(args) -> int:
    from .adapters.bilibili import BilibiliAdapter
    cfg, db = _ctx(args)
    adapter = BilibiliAdapter(cfg, db)
    result = {"available": adapter.probe().describe(), "running": adapter.app_running(),
              "trusted": adapter.accessibility_trusted()}
    if args.search:
        try:
            raw = adapter._search_ui(args.search)
            result["ui_cards"] = adapter._cards_from_result(raw)
        except Exception as exc:
            result["search_error"] = str(exc)
    if args.player:
        try:
            result["window"] = adapter._webarea()
            result["bvid"] = adapter._current_bvid()
            result["frame"] = adapter._player_frame()
            result["playing"] = adapter._playing_state()
            result["time_s"] = adapter._playback_seconds()
        except Exception as exc:
            result["player_error"] = str(exc)
    _print(result)
    db.close()
    return 0


def cmd_train_personalization(args) -> int:
    from .personalization import train
    cfg, db = _ctx(args)
    try:
        result = train(cfg, db)
    except Exception as exc:
        _print({"trained": False, "error": str(exc)})
        db.close()
        return 1
    _print({"trained": True, **result})
    db.close()
    return 0


def cmd_analyze_feedback(args) -> int:
    from .feedback_analysis import analyze
    cfg, db = _ctx(args)
    result = analyze(cfg, db, limit=args.limit, min_labels=args.min_labels)
    _print(result)
    db.close()
    return 0 if result.get("status") == "ok" else 1


def cmd_auto_train_feedback(args) -> int:
    from .auto_training import readiness, train_if_ready
    cfg, db = _ctx(args)
    _print(readiness(db) if args.status else train_if_ready(cfg, db))
    db.close()
    return 0


def cmd_export_training(args) -> int:
    from .training import export_dataset
    cfg, db = _ctx(args)
    try:
        result = export_dataset(cfg, db, Path(args.output).expanduser())
    except Exception as exc:
        _print({"exported": False, "error": str(exc)})
        db.close(); return 1
    _print({"exported": True, **result})
    db.close(); return 0


def cmd_personalization_status(args) -> int:
    from .personalization import Personalizer
    cfg, db = _ctx(args)
    _print(Personalizer(cfg).info())
    db.close()
    return 0


def cmd_training_status(args) -> int:
    from .training import maybe_remind, status
    cfg, db = _ctx(args)
    result = maybe_remind(db, display_notification=args.notify) if args.notify else status(db)
    _print(result)
    db.close()
    return 0


def cmd_ports(args) -> int:
    from .provisioning import list_serial_ports
    _print({"ports": [p.__dict__ for p in list_serial_ports()]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-assistant", description="Desk-Mate Bilibili voice assistant")
    parser.add_argument("--data-dir", help="override VOICE_ASSISTANT_DATA_DIR")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="create/show server identity and data root")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("doctor", help="check Mac, permissions, models, port and data")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("pair", help="provision one ESP32 over USB serial")
    p.add_argument("--port", help="serial device, e.g. /dev/cu.usbmodem0001")
    p.add_argument("--ssid", default="", help="2.4 GHz WiFi SSID (empty uses device SoftAP mode)")
    p.add_argument("--password", default="", help="WiFi password")
    p.add_argument("--manual-host", help="Mac LAN IP or hostname hint")
    p.add_argument("--name", default="Desk-Mate ESP32-C6")
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("run", help="run the authenticated WSS service")
    p.add_argument("--port", type=int)
    p.add_argument("--ble-address", help="Desk-Mate Bluetooth address (CoreBluetooth UUID on macOS)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="show devices and recent commands")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("devices", help="list provisioned devices")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("revoke", help="revoke a device token")
    p.add_argument("device_id")
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("backup", help="online SQLite backup")
    p.add_argument("--output")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("probe-bilibili", help="probe Accessibility and optionally search")
    p.add_argument("--search")
    p.add_argument("--player", action="store_true")
    p.set_defaults(func=cmd_probe_bilibili)

    p = sub.add_parser("train-personalization", help="train local intent model from recorded transcripts")
    p.set_defaults(func=cmd_train_personalization)

    p = sub.add_parser("analyze-feedback", help="analyze a 50-group feedback batch with a higher-order model")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--min-labels", type=int, default=10)
    p.set_defaults(func=cmd_analyze_feedback)

    p = sub.add_parser("auto-train-feedback", help="validation-gated automatic feedback training")
    p.add_argument("--status", action="store_true")
    p.set_defaults(func=cmd_auto_train_feedback)

    p = sub.add_parser("export-training", help="export local opt-in training samples/audio")
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_export_training)

    p = sub.add_parser("personalization-status", help="show local intent model status")
    p.set_defaults(func=cmd_personalization_status)

    p = sub.add_parser("training-status", help="show local training/sample reminder status")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(func=cmd_training_status)

    p = sub.add_parser("ports", help="list USB serial ports")
    p.set_defaults(func=cmd_ports)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
