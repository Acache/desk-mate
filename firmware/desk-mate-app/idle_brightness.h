#ifndef DESKMATE_IDLE_BRIGHTNESS_H
#define DESKMATE_IDLE_BRIGHTNESS_H
#include <stdint.h>

#define IDLE_BRIGHTNESS_DELAY_MS 10000u
#define IDLE_BRIGHTNESS_DEEP_DELAY_MS 60000u
#define IDLE_BRIGHTNESS_FADE_MS 1000u

typedef struct {
    uint32_t last_activity;
} IdleBrightness;

static inline void idle_brightness_activity(IdleBrightness *state, uint32_t now) {
    state->last_activity = now;
}

static inline uint8_t idle_brightness_level(const IdleBrightness *state, uint32_t now) {
    uint32_t elapsed = now - state->last_activity;
    if (elapsed >= IDLE_BRIGHTNESS_DEEP_DELAY_MS) {
        uint32_t fade = elapsed - IDLE_BRIGHTNESS_DEEP_DELAY_MS;
        if (fade >= IDLE_BRIGHTNESS_FADE_MS) return 25;
        return (uint8_t)(50u - 25u * fade / IDLE_BRIGHTNESS_FADE_MS);
    }
    if (elapsed <= IDLE_BRIGHTNESS_DELAY_MS) return 100;
    uint32_t fade = elapsed - IDLE_BRIGHTNESS_DELAY_MS;
    if (fade >= IDLE_BRIGHTNESS_FADE_MS) return 50;
    return (uint8_t)(100u - 50u * fade / IDLE_BRIGHTNESS_FADE_MS);
}
#endif
