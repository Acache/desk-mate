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
    assert(idle_brightness_level(&state, 54998) == 50);
    assert(idle_brightness_level(&state, 54999) == 50);
    assert(idle_brightness_level(&state, 55499) == 38);
    assert(idle_brightness_level(&state, 55999) == 25);
    /* The second fade starts 60 seconds after the last input, not after dimming. */
    idle_brightness_activity(&state, 100);
    assert(idle_brightness_level(&state, 60099) == 50);
    assert(idle_brightness_level(&state, 60100) == 50);
    assert(idle_brightness_level(&state, 60600) == 38);
    assert(idle_brightness_level(&state, 61100) == 25);
    assert(idle_brightness_level(&state, 3600100) == 25);
    /* Recover during the second fade, and from the lowest brightness. */
    idle_brightness_activity(&state, 60600);
    assert(idle_brightness_level(&state, 60600) == 100);
    assert(idle_brightness_level(&state, 120600) == 50);
    assert(idle_brightness_level(&state, 121600) == 25);
    idle_brightness_activity(&state, 121600);
    assert(idle_brightness_level(&state, 121600) == 100);
    assert(idle_brightness_level(&state, 131599) == 100);
    return 0;
}
