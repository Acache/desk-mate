"""Authenticated WSS control server for the ESP32 voice feature."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from .remote_session import serve_remote_session
from . import contracts
from .adapters.base import AdapterRegistry
from .config import AppConfig, load_config
from .contracts import CommandState, ErrorCode, TERMINAL_STATES
from .decision import DecisionAdapter, OllamaDecision
from .discovery import DiscoveryPublisher
from .pipeline import Pipeline, PipelineContext
from .sessions import CommandService, StageStore
from .stt import STTAdapter, build_stt
from .storage import Database, utc_now

log = logging.getLogger("voice_assistant.api")


def _json(model: Any) -> str:
    return model.model_dump_json()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def attach(self, device_id: str, ws: Any) -> None:
        async with self._lock:
            old = self._connections.get(device_id)
            self._connections[device_id] = ws
        if old is not None and old is not ws:
            try:
                await old.close(code=1008, reason="replaced by a newer device connection")
            except Exception:
                pass

    async def detach(self, device_id: str, ws: Any) -> None:
        async with self._lock:
            if self._connections.get(device_id) is ws:
                self._connections.pop(device_id, None)

    async def send(self, device_id: str, message: Any) -> bool:
        async with self._lock:
            ws = self._connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_text(_json(message))
            return True
        except Exception:
            return False

    async def close(self, device_id: str) -> None:
        async with self._lock:
            ws = self._connections.pop(device_id, None)
        if ws is not None:
            try:
                await ws.close(code=1008)
            except Exception:
                pass

    async def close_all(self) -> None:
        async with self._lock:
            devices = list(self._connections)
        for device_id in devices:
            await self.close(device_id)


class AppState:
    def __init__(
        self,
        cfg: AppConfig,
        db: Database,
        stages: StageStore,
        service: CommandService,
        stt: STTAdapter,
        decision: DecisionAdapter,
        registry: AdapterRegistry,
    ):
        self.cfg = cfg
        self.db = db
        self.stages = stages
        self.service = service
        self.stt = stt
        self.decision = decision
        self.registry = registry
        self.enabled_actions: tuple[str, ...] = contracts.ENABLED_ACTIONS
        self.early_stt: dict[str, object] = {}
        self.manager = ConnectionManager()
        self.tasks: set[asyncio.Task] = set()
        from .personalization import Personalizer
        self.personalizer = Personalizer(cfg)
        self.pipeline = Pipeline(PipelineContext(
            cfg=cfg, db=db, service=service, stt=stt, decision=decision,
            executor=None, enabled_actions=self.enabled_actions,
            personalizer=self.personalizer, early_stt=self.early_stt,
        ))
        from .executor import Executor
        self.pipeline.ctx.executor = Executor(db, registry)
        try:
            billi = registry.adapter_for("bilibili.pause")
            if hasattr(billi, "refresh_player_snapshot"):
                self.pipeline.ctx.refresh_player = billi.refresh_player_snapshot
            if hasattr(billi, "context_snapshot"):
                self.pipeline.ctx.context_reader = billi.context_snapshot
            self.bilibili_adapter = billi
        except Exception:
            self.bilibili_adapter = None
        self.discovery: DiscoveryPublisher | None = None
        self.started_at = utc_now()
        self.ble_status = {"enabled": False, "phase": "disabled"}
        self.ble_switching = False
        from .desktop_control import BleController
        self.ble_controller = BleController(self)

    async def notify_for(self, device_id: str):
        async def notify(message: Any) -> None:
            await self.manager.send(device_id, message)
        return notify

    def capabilities(self) -> tuple[str, ...]:
        caps = self.registry.capabilities()
        return tuple(a for a in self.enabled_actions if caps.has(a))

    def ready(self) -> tuple[bool, ErrorCode | None]:
        if not self.cfg.ready:
            return False, ErrorCode.READY_FALSE
        caps = self.capabilities()
        if not caps:
            return False, ErrorCode.CAPABILITY_UNAVAILABLE
        health = self.stt.health()
        if not health.get("available", False):
            return False, ErrorCode.MODEL_UNAVAILABLE
        dec = self.decision.health()
        if not dec.get("available", False):
            return False, ErrorCode.MODEL_UNAVAILABLE
        return True, None


def create_app(
    cfg: AppConfig | None = None,
    *,
    stt: STTAdapter | None = None,
    decision: DecisionAdapter | None = None,
    registry: AdapterRegistry | None = None,
    ble_address: str | None = None,
) -> FastAPI:
    cfg = cfg or load_config()
    if ble_address:
        from importlib.util import find_spec
        if find_spec("bleak") is None:
            raise RuntimeError("BLE requires: python -m pip install -r mac/requirements-ble.txt")
    cfg.ensure_layout()
    db = Database(cfg.db_path)
    reconcile = db.reconcile_startup()
    stages = StageStore(cfg, db)
    service = CommandService(cfg, db, stages)
    stt = stt or build_stt(cfg.stt_backend)
    decision = decision or OllamaDecision(cfg.ollama_model, cfg.ollama_url)
    if registry is None:
        from .adapters.bilibili import build_bilibili_adapter
        from .adapters.macos import MacSystemAdapter
        registry = AdapterRegistry()
        billi = build_bilibili_adapter(cfg, db)
        registry.register(billi, tuple(a for a in contracts.ALL_ACTIONS if a.startswith("bilibili.")))
        # R2 system actions are registered but remain disabled until enabled.
        registry.register(MacSystemAdapter(cfg, db),
                          tuple(a for a in contracts.R2_ACTIONS if a.startswith("system.")))
    state = AppState(cfg, db, stages, service, stt, decision, registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        warm_task = None
        if hasattr(decision, "prewarm"):
            warm_task = asyncio.create_task(asyncio.to_thread(decision.prewarm))
        # Prewarm the local STT worker; model loading is not allowed to block
        # the socket event loop (it runs in a worker thread/process).
        try:
            await asyncio.to_thread(stt.health)
        except Exception as exc:
            log.warning("STT prewarm failed: %s", exc)
        if getattr(state, "bilibili_adapter", None) is not None:
            try:
                await asyncio.to_thread(state.bilibili_adapter.start_daemon)
            except Exception as exc:
                log.warning("AX daemon prewarm failed: %s", exc)
        if getattr(state, "personalizer", None) is not None:
            try:
                await asyncio.to_thread(state.personalizer._load)
            except Exception as exc:
                log.warning("personalizer prewarm failed: %s", exc)

        async def training_reminder_loop() -> None:
            from .training import maybe_remind
            while True:
                try:
                    result = await asyncio.to_thread(maybe_remind, db, True)
                    if result.get("samples", 0):
                        log.info("training status: %s", result)
                except Exception as exc:
                    log.warning("training reminder failed: %s", exc)
                await asyncio.sleep(6 * 3600)

        state.training_task = asyncio.create_task(training_reminder_loop())
        if os.environ.get("VOICE_ASSISTANT_DISABLE_DISCOVERY") != "1":
            state.discovery = DiscoveryPublisher(cfg)
            try:
                state.discovery.start()
            except Exception as exc:
                log.warning("mDNS discovery not started: %s", exc)
        control_server = None
        control_socket = None
        if os.environ.get("VOICE_ASSISTANT_DESKTOP") == "1":
            from .desktop_control import start_control_server
            control_server,control_socket = await start_control_server(state)
        if ble_address:
            await state.ble_controller.select(ble_address,persist=False)
        yield
        if control_server:
            control_server.close()
            await control_server.wait_closed()
            control_socket.unlink(missing_ok=True)
        await state.ble_controller.close()
        if warm_task:
            warm_task.cancel()
        if getattr(state, "training_task", None):
            state.training_task.cancel()
        for task in list(state.tasks):
            task.cancel()
        if state.discovery:
            state.discovery.stop()
        await state.manager.close_all() if hasattr(state.manager, "close_all") else None
        db.close()

    app = FastAPI(title="Desk-Mate Voice Assistant", version="1.0", lifespan=lifespan)
    app.state.voice = state

    @app.get("/healthz")
    async def healthz():
        from .adapters.cdp_player import status as cdp_status
        (ready, code), stt_status, decision_status, cdp = await asyncio.gather(
            asyncio.to_thread(state.ready), asyncio.to_thread(stt.health),
            asyncio.to_thread(decision.health), asyncio.to_thread(cdp_status),
        )
        return {
            "ok": True,
            "ready": ready,
            "code": code.value if code else None,
            "server_id": cfg.server.server_id if cfg.server else None,
            "tls_name": cfg.server.tls_name if cfg.server else None,
            "stt": stt_status,
            "decision": decision_status,
            "cdp": cdp,
            "capabilities": list(state.capabilities()),
            "reconciled": reconcile,
            "active_command": (db.active_command() or {}).get("id"),
            "started_at": state.started_at,
            "ble": dict(state.ble_status),
        }

    @app.get("/v1/status/{command_id}")
    async def http_status(command_id: str):
        row = db.get_command(command_id)
        if not row:
            return JSONResponse({"known": False, "code": "COMMAND_NOT_FOUND"}, status_code=404)
        msg = state.pipeline.result_message(command_id)
        return {
            "known": True,
            "state": row["state"],
            "terminal": CommandState(row["state"]) in TERMINAL_STATES,
            "result": msg.model_dump(mode="json") if msg else None,
        }

    @app.websocket("/v1/remote")
    async def remote(ws: WebSocket):
        headers = {k.lower(): v for k, v in ws.headers.items()}
        origin = headers.get("origin")
        if origin and origin not in {"null"}:
            await ws.close(code=1008, reason="browser origins are not allowed")
            return
        auth = headers.get("authorization", "")
        device_id = headers.get("x-device-id", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not device_id or not token or not db.authenticate_device(device_id, token):
            await ws.close(code=1008, reason="authentication failed")
            return
        await ws.accept()
        await serve_remote_session(state, ws, device_id)

    return app


def run_server(cfg: AppConfig | None = None, *, ble_address: str | None = None) -> None:
    import uvicorn

    cfg = cfg or load_config()
    app = create_app(cfg, ble_address=ble_address)
    if cfg.server is None:
        raise SystemExit("server identity missing")
    # uvicorn configures only its own loggers; without this the bridge's INFO
    # lines (authentication, session lifecycle) never reach the terminal and
    # only warnings are visible.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("voice_assistant").setLevel(logging.INFO)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=cfg.port,
        ssl_certfile=str(cfg.server.server_cert_path),
        ssl_keyfile=str(cfg.server.server_key_path),
        log_level="info",
        access_log=False,
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )
