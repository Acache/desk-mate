"""IMA ADPCM 4-bit audio codec shared by the BLE voice bridge.

Why this exists
---------------
Raw 16 kHz mono s16 PCM needs 32 KB/s. The Desk-Mate BLE link sustains about
21-23 KB/s in the field (the synthetic lab peaked at 31.9 KB/s, i.e. no
margin), while the device only buffers 1.28 s. Any recording longer than that
silently loses frames — including speech — so a user who waits before talking
gets chopped audio even though nothing is wrong with the microphone.

Encoding each 20 ms / 640-byte PCM frame to a self-contained 164-byte block
drops the demand to 8 KB/s (about 50 BLE packets/s instead of 150), which the
same link carries with roughly 2x headroom. Latency, pauses and long sentences
no longer matter; the device ring never overflows.

Block layout (little endian)::

    0-1  predictor (s16) at the start of the block
    2    step index (0..88)
    3    reserved (0)
    4+   160 bytes, two 4-bit nibbles per byte, low nibble first

The encoder keeps its state across blocks for quality; each block still
carries the state in its header so a decoder can resynchronise. Decoding is
deterministic, so the firmware can hash the reconstructed PCM and the host
reproduces byte-identical audio for the existing seal/commit SHA check.
"""
from __future__ import annotations

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
    45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
    209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
    796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499,
    2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845,
    8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385,
    24623, 27086, 29794, 32767,
)
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)

SAMPLES_PER_BLOCK = 320          # 20 ms at 16 kHz
NIBBLES_PER_BLOCK = SAMPLES_PER_BLOCK // 2
BLOCK_HEADER = 4
BLOCK_BYTES = BLOCK_HEADER + NIBBLES_PER_BLOCK   # 164
PCM_BYTES_PER_BLOCK = SAMPLES_PER_BLOCK * 2      # 640


def _step(code: int, predictor: int, index: int) -> tuple[int, int]:
    """Advance one IMA ADPCM sample; shared by encode and decode."""
    step = STEP_TABLE[index]
    diff = step >> 3
    if code & 4:
        diff += step
    if code & 2:
        diff += step >> 1
    if code & 1:
        diff += step >> 2
    if code & 8:
        predictor -= diff
    else:
        predictor += diff
    if predictor > 32767:
        predictor = 32767
    elif predictor < -32768:
        predictor = -32768
    index += INDEX_TABLE[code]
    if index < 0:
        index = 0
    elif index > 88:
        index = 88
    return predictor, index


def decode_block(data: bytes) -> bytes:
    """Decode one 164-byte block into 640 bytes of s16le PCM."""
    if len(data) != BLOCK_BYTES:
        raise ValueError('adpcm block must be 164 bytes')
    predictor = int.from_bytes(data[0:2], 'little', signed=True)
    index = data[2]
    out = bytearray(PCM_BYTES_PER_BLOCK)
    pos = 0
    for byte in data[BLOCK_HEADER:]:
        for code in (byte & 0x0F, byte >> 4):
            predictor, index = _step(code, predictor, index)
            out[pos] = predictor & 0xFF
            out[pos + 1] = (predictor >> 8) & 0xFF
            pos += 2
    return bytes(out)


def _quantize(sample: int, predictor: int, index: int) -> int:
    step = STEP_TABLE[index]
    diff = sample - predictor
    if diff < 0:
        code, diff = 8, -diff
    else:
        code = 0
    if diff >= step:
        code |= 4
        diff -= step
    if diff >= (step >> 1):
        code |= 2
        diff -= step >> 1
    if diff >= (step >> 2):
        code |= 1
    return code


def encode_block(pcm: bytes, state: tuple[int, int] = (0, 0)) -> tuple[bytes, tuple[int, int], bytes]:
    """Encode 640 bytes of s16le PCM.

    Returns (block, new_state, reconstructed_pcm); the reconstruction is what a
    decoder will produce expensively, so callers hash it for the existing seal
    SHA instead of the pre-encode samples.
    """
    if len(pcm) != PCM_BYTES_PER_BLOCK:
        raise ValueError('adpcm input must be 640 bytes')
    predictor, index = state
    if not 0 <= index <= 88:
        raise ValueError('adpcm step index out of range')
    block = bytearray(BLOCK_BYTES)
    block[0:2] = int(predictor).to_bytes(2, 'little', signed=True)
    block[2] = index
    block[3] = 0
    recon = bytearray(PCM_BYTES_PER_BLOCK)
    out = BLOCK_HEADER
    for i in range(0, SAMPLES_PER_BLOCK, 2):
        codes = []
        for j in (i, i + 1):
            sample = int.from_bytes(pcm[2 * j:2 * j + 2], 'little', signed=True)
            code = _quantize(sample, predictor, index)
            predictor, index = _step(code, predictor, index)
            recon[2 * j] = predictor & 0xFF
            recon[2 * j + 1] = (predictor >> 8) & 0xFF
            codes.append(code)
        block[out] = codes[0] | (codes[1] << 4)
        out += 1
    return bytes(block), (predictor, index), bytes(recon)
