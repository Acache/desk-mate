#ifndef DESKMATE_POWER_HOLD_H
#define DESKMATE_POWER_HOLD_H
#include <stdbool.h>
#include <stdint.h>

#define POWER_HOLD_DELAY_MS 1000u
#define POWER_HOLD_PROGRESS_MS 3000u
#define POWER_HOLD_REWIND_MS 2000u
#define POWER_KEY_DOWN 1u
#define POWER_KEY_UP 2u
#define POWER_KEY_SHORT 4u

typedef enum { POWER_IDLE, POWER_HOLD, POWER_REWIND } PowerPhase;
typedef struct {
    PowerPhase phase;
    bool down, suppress_short;
    uint32_t pressed_at, released_at, progress_ms, released_progress, rewind_ms;
    uint8_t opacity;
} PowerHold;

static inline bool power_hold_visible(const PowerHold *p) {
    return p->phase == POWER_REWIND ||
        (p->phase == POWER_HOLD && p->suppress_short);
}
static inline void power_hold_tick(PowerHold *p, uint32_t now) {
    if (p->phase == POWER_HOLD) {
        uint32_t elapsed = now - p->pressed_at;
        if (elapsed >= POWER_HOLD_DELAY_MS) {
            p->suppress_short = true;
            p->progress_ms = elapsed - POWER_HOLD_DELAY_MS;
            if (p->progress_ms > POWER_HOLD_PROGRESS_MS) p->progress_ms = POWER_HOLD_PROGRESS_MS;
        }
        p->opacity = 255;
    } else if (p->phase == POWER_REWIND) {
        uint32_t elapsed = now - p->released_at;
        if (elapsed < p->rewind_ms) {
            p->progress_ms = p->released_progress * (p->rewind_ms - elapsed) / p->rewind_ms;
        } else {
            p->progress_ms = 0;
            // Group-opacity fading repaints the entire display on this panel.
            // Once the reverse countdown reaches zero, dismiss immediately.
            p->phase = POWER_IDLE;
        }
    }
}
/* Returns true only for page-owned taps. A hold's delayed short IRQ is swallowed.
 * Both edge bits in one snapshot are unordered: finish an existing hold, or
 * treat an idle complete tap as down/up. Never leave a phantom press latched. */
static inline bool power_hold_input(PowerHold *p, uint32_t now, uint8_t events) {
    power_hold_tick(p, now);
    if ((events & POWER_KEY_DOWN) && !p->down) {
        p->down = true; p->suppress_short = false;
        p->phase = POWER_HOLD; p->pressed_at = now;
        p->progress_ms = 0; p->opacity = 255;
    }
    if ((events & POWER_KEY_UP) && p->down) {
        p->down = false;
        if (p->suppress_short) {
            p->phase = POWER_REWIND; p->released_at = now;
            p->released_progress = p->progress_ms;
            p->rewind_ms = p->progress_ms * POWER_HOLD_REWIND_MS / POWER_HOLD_PROGRESS_MS;
            if (p->rewind_ms < 200) p->rewind_ms = 200;
        } else p->phase = POWER_IDLE;
    }
    return (events & POWER_KEY_SHORT) && !p->suppress_short;
}
#endif
