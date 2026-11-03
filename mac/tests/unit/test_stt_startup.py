import io
import subprocess
import threading
from unittest.mock import Mock

from voice_assistant.stt import SenseVoiceWorker, STTError


def test_worker_failure_returns_error_instead_of_deadlocking(monkeypatch):
    proc = Mock()
    proc.poll.return_value = None
    proc.stdout = io.StringIO('{"ready":false,"error":"offline model missing"}\n')
    proc.stderr = io.StringIO("loader diagnostic\n"*10000)
    monkeypatch.setattr(subprocess,"Popen",lambda *a,**kw:proc)
    worker = SenseVoiceWorker()
    errors = []
    def start():
        try:
            worker.start()
        except STTError as exc:
            errors.append(exc.summary)
    thread = threading.Thread(target=start,daemon=True)
    thread.start(); thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == ["offline model missing"]
    proc.kill.assert_called_once()
