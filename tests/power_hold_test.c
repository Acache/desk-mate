#include <assert.h>
#include <stdio.h>
#include "../firmware/desk-mate-app/power_hold.h"
int main(void) {
    PowerHold p = {0};
    assert(!power_hold_input(&p, 10, POWER_KEY_DOWN));
    assert(power_hold_input(&p, 100, POWER_KEY_UP | POWER_KEY_SHORT));
    assert(p.phase == POWER_IDLE);
    power_hold_input(&p, 200, POWER_KEY_DOWN);
    power_hold_tick(&p, 1199); assert(!power_hold_visible(&p));
    power_hold_tick(&p, 1200); assert(power_hold_visible(&p) && p.progress_ms == 0);
    power_hold_tick(&p, 2700); assert(p.progress_ms == 1500);
    assert(!power_hold_input(&p, 2700, POWER_KEY_UP | POWER_KEY_SHORT));
    assert(p.phase == POWER_REWIND && p.rewind_ms == 1000);
    power_hold_tick(&p, 3200); assert(p.progress_ms == 750);
    power_hold_tick(&p, 3699); assert(power_hold_visible(&p) && p.opacity == 255);
    power_hold_tick(&p, 3700); assert(p.progress_ms == 0 && p.phase == POWER_IDLE);
    assert(!power_hold_visible(&p)); /* dismiss at zero; no group-opacity fade */
    power_hold_tick(&p, 4700); assert(!power_hold_visible(&p));
    assert(!power_hold_input(&p, 4800, POWER_KEY_SHORT)); /* delayed cancelled tap */
    power_hold_input(&p, 5000, POWER_KEY_DOWN);
    power_hold_tick(&p, 9000); assert(p.progress_ms == 3000);
    power_hold_tick(&p, 9500); assert(p.progress_ms == 3000); /* saturated */
    power_hold_input(&p, 9500, POWER_KEY_UP);
    assert(p.rewind_ms == 2000);
    power_hold_input(&p, 9600, POWER_KEY_DOWN); /* fresh hold resets old progress */
    assert(!power_hold_visible(&p) && p.progress_ms == 0);
    power_hold_tick(&p, 10600); assert(power_hold_visible(&p));
    power_hold_input(&p, 10610, POWER_KEY_UP);
    power_hold_tick(&p, 10810); assert(p.phase == POWER_IDLE);
    power_hold_input(&p, 10900, POWER_KEY_DOWN);
    assert(power_hold_input(&p, 11000, POWER_KEY_UP | POWER_KEY_SHORT));
    assert(p.phase == POWER_IDLE);
    assert(power_hold_input(&p, 12000, POWER_KEY_DOWN | POWER_KEY_UP | POWER_KEY_SHORT));
    assert(!p.down); /* complete tap inside one polling interval */
    p = (PowerHold){0};
    power_hold_input(&p, UINT32_MAX - 499, POWER_KEY_DOWN);
    power_hold_tick(&p, 500); assert(power_hold_visible(&p) && p.progress_ms == 0);
    power_hold_input(&p, 3500, POWER_KEY_UP); assert(p.progress_ms == 3000);
    power_hold_tick(&p, 6500); assert(!power_hold_visible(&p));
    puts("power hold: OK");
}
