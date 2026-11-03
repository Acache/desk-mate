"""FunASR SenseVoice worker. Runs as a separate process so a timeout can kill it."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="iic/SenseVoiceSmall")
    args = parser.parse_args()
    # Third-party loaders print banners to stdout; the parent protocol owns
    # stdout. Temporarily route those banners to stderr.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        from funasr import AutoModel  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional runtime
        sys.stdout = real_stdout
        emit({"ready": False, "error": f"funasr unavailable: {exc}"})
        return 1
    try:
        model = AutoModel(
            model=args.model,
            disable_update=True,
            trust_remote_code=False,
            device="cpu",
        )
    except Exception as exc:
        sys.stdout = real_stdout
        emit({"ready": False, "error": f"model load failed: {exc}"})
        return 1
    digest = os.environ.get("VOICE_STT_MODEL_DIGEST") or args.model
    sys.stdout = real_stdout
    emit({"ready": True, "model_digest": digest})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            path = Path(request["audio_path"])
            if not path.is_file():
                raise ValueError("audio file missing")
            started = time.monotonic()
            result = model.generate(
                input=str(path),
                cache={},
                language="auto",
                use_itn=True,
                batch_size_s=60,
                merge_vad=True,
                merge_length_s=15,
            )
            text = ""
            if isinstance(result, list) and result:
                text = str(result[0].get("text", ""))
            elif isinstance(result, dict):
                text = str(result.get("text", ""))
            emit({
                "text": text,
                "language": None,
                "segments": [],
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model_digest": digest,
            })
        except Exception as exc:
            emit({"error": f"transcription failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
