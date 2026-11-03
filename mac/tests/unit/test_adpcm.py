"""IMA ADPCM codec: golden vector and roundtrip quality (synthetic audio only)."""
import numpy as np
import pytest
from voice_assistant.adpcm import (
    BLOCK_BYTES, PCM_BYTES_PER_BLOCK, decode_block, encode_block,
)

GOLDEN = bytes.fromhex(
    "0000000077f7f7777f2c1e10944da91001f2c1b2d741980a7a91b1e2a4a129924c6e"
    "4e3c89d4819044093f1082f0a8b484b97199a2a3daa4c38501ea051bb8c7982003f0"
    "b5498bb5c2a29873b8b4825ccb05a002d0282fa1b7499b2198798d04f1a592a0878d"
    "a6c22a7a3c3e28384f3a2fa39a99175c3fd479ca4288b9e7519c795c6e6e4bda953"
    "b0da601f1c3d581b4b1b611c920132e3d38f192910c163b9ca4b44a88"
)


def lcg_pcm():
    x, out = 12345, bytearray()
    for _ in range(320):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        sample = ((x >> 16) & 0xFFFF) - 32768
        out += (sample & 0xFFFF).to_bytes(2, 'little')
    return bytes(out)


def test_golden_vector_matches_firmware():
    block, state, recon = encode_block(lcg_pcm(), (0, 0))
    assert block == GOLDEN
    assert state == (2662, 86)
    decoded = decode_block(GOLDEN)
    assert decoded == recon
    assert len(decoded) == PCM_BYTES_PER_BLOCK
    assert decoded[:8] == bytes.fromhex('0b0029006800e0ff')
    assert decoded[-8:] == bytes.fromhex('eab1f128f218660a')


def test_block_size_and_rejected_lengths():
    with pytest.raises(ValueError):
        decode_block(GOLDEN[:-1])
    with pytest.raises(ValueError):
        encode_block(b'\x00' * 10)
    assert len(GOLDEN) == BLOCK_BYTES == 164


def test_roundtrip_keeps_speech_band_energy():
    t = np.arange(320) / 16000.0
    pcm = (6000 * np.sin(2 * np.pi * 220 * t) + 1500 * np.sin(2 * np.pi * 900 * t))
    raw = np.clip(pcm, -32768, 32767).astype('<i2').tobytes()
    block, _, recon = encode_block(raw, (0, 0))
    a = np.frombuffer(raw, dtype='<i2').astype(float)
    b = np.frombuffer(recon, dtype='<i2').astype(float)
    snr = 10 * np.log10((a ** 2).sum() / (((a - b) ** 2).sum() + 1e-9))
    assert snr > 15.0
    assert decode_block(block) == recon
