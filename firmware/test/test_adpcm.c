#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../desk-mate-app/src/bili_voice/adpcm.h"

static void put_s16(uint8_t *p, int32_t v) {
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)((v >> 8) & 0xff);
}

static int32_t get_s16(const uint8_t *p) {
    return (int32_t)(int16_t)(uint16_t)(p[0] | (p[1] << 8));
}

static void from_hex(const char *hex, uint8_t *out, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        unsigned value = 0;
        assert(sscanf(hex + 2 * i, "%2x", &value) == 1);
        out[i] = (uint8_t)value;
    }
}

/* Deterministic wideband pattern: Python reference produces the same bytes. */
static void lcg_pcm(uint8_t *pcm) {
    uint32_t x = 12345u;
    for (int i = 0; i < 320; ++i) {
        x = x * 1103515245u + 12345u;
        int32_t sample = (int32_t)((x >> 16) & 0xFFFFu) - 32768;
        put_s16(pcm + 2 * i, sample);
    }
}

static void golden_vector(void) {
    static const char *golden =
        "0000000077f7f7777f2c1e10944da91001f2c1b2d741980a7a91b1e2a4a129924c6e"
        "4e3c89d4819044093f1082f0a8b484b97199a2a3daa4c38501ea051bb8c7982003f0"
        "b5498bb5c2a29873b8b4825ccb05a002d0282fa1b7499b2198798d04f1a592a0878d"
        "a6c22a7a3c3e28384f3a2fa39a99175c3fd479ca4288b9e7519c795c6e6e4bda953"
        "b0da601f1c3d581b4b1b611c920132e3d38f192910c163b9ca4b44a88";
    uint8_t pcm[ADPCM_BLOCK_PCM_BYTES], block[ADPCM_BLOCK_BYTES];
    uint8_t expect[ADPCM_BLOCK_BYTES], recon[ADPCM_BLOCK_PCM_BYTES], decoded[ADPCM_BLOCK_PCM_BYTES];
    lcg_pcm(pcm);
    adpcm_state_t state;
    adpcm_reset(&state);
    assert(adpcm_encode_block(pcm, sizeof(pcm), block, &state, recon) == ADPCM_BLOCK_BYTES);
    from_hex(golden, expect, sizeof(expect));
    assert(memcmp(block, expect, sizeof(expect)) == 0);
    assert(state.predictor == 2662 && state.index == 86);
    assert(adpcm_decode_block(expect, sizeof(expect), decoded) == ADPCM_BLOCK_PCM_BYTES);
    assert(memcmp(decoded, recon, sizeof(decoded)) == 0);
    const uint8_t first[8] = {0x0b, 0x00, 0x29, 0x00, 0x68, 0x00, 0xe0, 0xff};
    const uint8_t last[8] = {0xea, 0xb1, 0xf1, 0x28, 0xf2, 0x18, 0x66, 0x0a};
    assert(memcmp(decoded, first, 8) == 0);
    assert(memcmp(decoded + sizeof(decoded) - 8, last, 8) == 0);
}

static void roundtrip_quality(void) {
    uint8_t pcm[ADPCM_BLOCK_PCM_BYTES], block[ADPCM_BLOCK_BYTES], recon[ADPCM_BLOCK_PCM_BYTES];
    double signal = 0, noise = 0;
    for (int i = 0; i < 320; ++i) {
        double t = (double)i / 16000.0;
        int32_t sample = (int32_t)(6000.0 * sin(2 * M_PI * 220.0 * t) +
                                   1500.0 * sin(2 * M_PI * 900.0 * t));
        put_s16(pcm + 2 * i, sample);
    }
    adpcm_state_t state;
    adpcm_reset(&state);
    assert(adpcm_encode_block(pcm, sizeof(pcm), block, &state, recon) == ADPCM_BLOCK_BYTES);
    for (int i = 0; i < 320; ++i) {
        double a = get_s16(pcm + 2 * i), b = get_s16(recon + 2 * i);
        signal += a * a;
        noise += (a - b) * (a - b);
    }
    double snr = 10.0 * log10(signal / (noise + 1e-9));
    assert(snr > 15.0);
    /* The header repeats the state so an independent decoder stays in sync. */
    adpcm_state_t second = state;
    assert(adpcm_encode_block(pcm, sizeof(pcm), block, &second, recon) == ADPCM_BLOCK_BYTES);
    assert(adpcm_decode_block(block, sizeof(block), pcm) == ADPCM_BLOCK_PCM_BYTES);
    assert(memcmp(pcm, recon, sizeof(pcm)) == 0);
    uint8_t bad[ADPCM_BLOCK_BYTES] = {0};
    assert(adpcm_decode_block(bad, 100, pcm) == 0);
    assert(adpcm_encode_block(pcm, 100, block, &state, recon) == 0);
}

int main(void) {
    golden_vector();
    roundtrip_quality();
    puts("adpcm: OK");
    return 0;
}
