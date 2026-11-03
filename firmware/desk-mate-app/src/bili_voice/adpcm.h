#ifndef DESKMATE_ADPCM_H
#define DESKMATE_ADPCM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* One 20 ms voice frame: 320 mono samples at 16 kHz. */
#define ADPCM_BLOCK_PCM_BYTES 640
#define ADPCM_BLOCK_BYTES 164   /* 4-byte header + 160 nibble bytes */

typedef struct {
    int16_t predictor;
    uint8_t index;
} adpcm_state_t;

void adpcm_reset(adpcm_state_t *state);

/* Encodes one 640-byte s16le frame. `reconstructed` receives the decoder's
 * output so the caller can hash exactly what the host will store. */
size_t adpcm_encode_block(const uint8_t *pcm, size_t pcm_bytes, uint8_t *out,
                          adpcm_state_t *state, uint8_t *reconstructed);

size_t adpcm_decode_block(const uint8_t *in, size_t bytes, uint8_t *pcm);

#ifdef __cplusplus
}
#endif
#endif
