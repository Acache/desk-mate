/* IMA ADPCM 4:1 frame codec for the BLE voice link.
 *
 * The raw stream needs 32 KB/s; the link sustains ~21 KB/s, so a device with a
 * 1.28 s buffer drops speech whenever the user pauses before talking. Encoding
 * each 640-byte frame to 164 bytes cuts the demand to 8 KB/s.
 *
 * Block: predictor s16le, step index u8, reserved u8, then 160 nibble bytes
 * (low nibble = earlier sample). The encoder carries its state across blocks
 * but every block repeats it in the header so decoding can resynchronise.
 */
#include "adpcm.h"

static const int16_t kStep[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
    45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
    209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
    796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272,
    2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
    7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350,
    22385, 24623, 27086, 29794, 32767,
};

static const int8_t kIndex[16] = {-1, -1, -1, -1, 2, 4, 6, 8,
                                  -1, -1, -1, -1, 2, 4, 6, 8};

static void step_once(int code, int32_t *predictor, int *index) {
    int32_t step = kStep[*index];
    int32_t diff = step >> 3;
    if (code & 4) diff += step;
    if (code & 2) diff += step >> 1;
    if (code & 1) diff += step >> 2;
    *predictor += (code & 8) ? -diff : diff;
    if (*predictor > 32767) *predictor = 32767;
    else if (*predictor < -32768) *predictor = -32768;
    *index += kIndex[code];
    if (*index < 0) *index = 0;
    else if (*index > 88) *index = 88;
}

static int quantize(int32_t sample, int32_t predictor, int index) {
    int32_t diff = sample - predictor;
    int code = 0;
    if (diff < 0) {
        code = 8;
        diff = -diff;
    }
    int32_t step = kStep[index];
    if (diff >= step) {
        code |= 4;
        diff -= step;
    }
    if (diff >= (step >> 1)) {
        code |= 2;
        diff -= step >> 1;
    }
    if (diff >= (step >> 2)) code |= 1;
    return code;
}

static int32_t read_s16(const uint8_t *p) {
    return (int32_t)(int16_t)(uint16_t)(p[0] | (p[1] << 8));
}

static void write_s16(uint8_t *p, int32_t value) {
    p[0] = (uint8_t)(value & 0xff);
    p[1] = (uint8_t)((value >> 8) & 0xff);
}

void adpcm_reset(adpcm_state_t *state) {
    if (!state) return;
    state->predictor = 0;
    state->index = 0;
}

size_t adpcm_encode_block(const uint8_t *pcm, size_t pcm_bytes, uint8_t *out,
                          adpcm_state_t *state, uint8_t *reconstructed) {
    if (!pcm || !out || !state || !reconstructed || pcm_bytes != ADPCM_BLOCK_PCM_BYTES)
        return 0;
    int32_t predictor = state->predictor;
    int index = state->index;
    out[0] = (uint8_t)(predictor & 0xff);
    out[1] = (uint8_t)((predictor >> 8) & 0xff);
    out[2] = (uint8_t)index;
    out[3] = 0;
    size_t at = 4;
    for (int i = 0; i < 320; i += 2) {
        int codes[2];
        for (int j = 0; j < 2; ++j) {
            int32_t sample = read_s16(pcm + 2 * (i + j));
            codes[j] = quantize(sample, predictor, index);
            step_once(codes[j], &predictor, &index);
            write_s16(reconstructed + 2 * (i + j), predictor);
        }
        out[at++] = (uint8_t)(codes[0] | (codes[1] << 4));
    }
    state->predictor = (int16_t)predictor;
    state->index = (uint8_t)index;
    return ADPCM_BLOCK_BYTES;
}

size_t adpcm_decode_block(const uint8_t *in, size_t bytes, uint8_t *pcm) {
    if (!in || !pcm || bytes != ADPCM_BLOCK_BYTES) return 0;
    int32_t predictor = (int32_t)(int16_t)(uint16_t)(in[0] | (in[1] << 8));
    int index = in[2] > 88 ? 88 : in[2];
    size_t pos = 0;
    for (size_t i = 4; i < bytes; ++i) {
        uint8_t packed = in[i];
        for (int half = 0; half < 2; ++half) {
            step_once(half == 0 ? (packed & 0x0f) : (packed >> 4), &predictor, &index);
            write_s16(pcm + pos, predictor);
            pos += 2;
        }
    }
    return pos;
}
