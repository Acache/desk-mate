"""USB bundle construction only; no serial, network or end-to-end execution."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from voice_assistant.provisioning import build_bundle


def test_bundle_adds_host_label_without_changing_v1_identity(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "Study-Mac.local")
    cfg = SimpleNamespace(
        port=8765,
        server=SimpleNamespace(
            server_id="11111111-1111-4111-8111-111111111111",
            tls_name="voice.local",
            ca_pem="-----BEGIN CERTIFICATE-----test-----END CERTIFICATE-----",
        ),
    )
    bundle, device_id, token = build_bundle(cfg, None, "Home Wi-Fi", "example-password", trusted_time=1800000000)
    assert bundle["schema_version"] == 1
    assert bundle["server"]["display_name"] == "Study-Mac"
    assert bundle["device_id"] == device_id and bundle["device_token"] == token
    assert bundle["server_id"] == cfg.server.server_id
    assert bundle["wifi"]["ssid"] == "Home Wi-Fi"
