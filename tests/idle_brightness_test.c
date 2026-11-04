#include <assert.h>
#include <stdint.h>
#include "../firmware/desk-mate-app/idle_brightness.h"

int main(void) {
    IdleBrightness state = {0};
    idle_brightness_activity(&state, 100);
    assert(idle_brightness_level(&state, 10099) == 100);
    assert(idle_brightness_level(&state, 10100) == 100);
    assert(idle_brightness_level(&state, 10600) == 75);
    assert(idle_brightness_level(&state, 11100) == 50);
    assert(idle_brightness_level(&state, 50000) == 50);
    /* An input interrupts either an ongoing fade or an already dimmed panel. */
    idle_brightness_activity(&state, 50000);
    assert(idle_brightness_level(&state, 50000) == 100);
    assert(idle_brightness_level(&state, 60500) == 75);
    idle_brightness_activity(&state, 60500);
    assert(idle_brightness_level(&state, 60500) == 100);
    /* Repeated activity while held keeps full brightness; release resets idle. */
    for (uint32_t now = 61000; now <= 81000; now += 20) {
        idle_brightness_activity(&state, now);
        assert(idle_brightness_level(&state, now) == 100);
    }
    idle_brightness_activity(&state, 81020);
    assert(idle_brightness_level(&state, 91019) == 100);
    assert(idle_brightness_level(&state, 92020) == 50);
    /* Elapsed time must work across the 32-bit tick wrap. */
    idle_brightness_activity(&state, UINT32_MAX - 5000u);
    assert(idle_brightness_level(&state, 4998) == 100);
    assert(idle_brightness_level(&state, 5499) == 75);
    assert(idle_brightness_level(&state, 5999) == 50);
    return 0;
}
