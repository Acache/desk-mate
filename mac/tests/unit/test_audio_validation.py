"""Speech-gate tests use synthetic PCM only; no microphone or user audio."""
import wave
import numpy as np
import pytest
from voice_assistant.stt import STTError, validate_audio

RATE = 16000


def write_wav(path, samples):
    data = np.clip(np.asarray(samples, dtype=np.float64) * 32768, -32768, 32767).astype('<i2')
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data.tobytes())
    return path


def tone(seconds, amplitude, freq=300.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return amplitude * np.sin(2 * np.pi * freq * t)


def test_silent_lead_in_still_accepts_real_speech(tmp_path):
    # 3.4 s of digital silence, then 0.6 s of quiet speech-level tone: whole
    # file RMS is below the legacy 0.0025 floor but the active part is not.
    samples = np.concatenate([np.zeros(int(3.4 * RATE)), tone(0.6, 0.0085)])
    result = validate_audio(write_wav(tmp_path / 'lead.wav', samples))
    assert result.rms < 0.0025
    assert result.active_ms >= 300 and result.active_rms >= 0.005
    assert result.is_speech_like() is True


def test_short_click_is_not_speech(tmp_path):
    samples = np.concatenate([np.zeros(int(2 * RATE)), tone(0.1, 0.01), np.zeros(int(1 * RATE))])
    result = validate_audio(write_wav(tmp_path / 'click.wav', samples))
    assert result.active_ms < 300
    assert result.is_speech_like() is False


def test_silence_is_not_speech(tmp_path):
    result = validate_audio(write_wav(tmp_path / 'silence.wav', np.zeros(3 * RATE)))
    assert result.quality_label == 'silent'
    assert result.is_speech_like() is False


def test_short_recording_is_rejected(tmp_path):
    with pytest.raises(STTError):
        validate_audio(write_wav(tmp_path / 'short.wav', tone(0.2, 0.2)))
