#pragma once
#include <stdbool.h>
#include <stdint.h>

/* A new companion subscription needs fresh wire counters even when macOS
 * keeps its physical HID connection. Re-registering the same subscription
 * for notification delivery recovery must preserve the current session. */
static inline bool bv_subscription_starts_session(uint32_t previous,uint32_t next) {
    return next!=0&&previous!=next;
}
