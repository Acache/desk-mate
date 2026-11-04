# Global idle brightness

## Goal and behavior
All three main pages and their overlays share one inactivity timer. After 10 seconds without physical input, fade the panel from its normal brightness to 50% over 1 second. Any touch (including a swipe or sustained press), BOOT, KEY, or PWR input restores normal brightness immediately and restarts inactivity timing. The triggering input continues to perform its existing action. Held inputs keep the panel bright until release.

## Approach
Use the existing SH8601 hardware brightness command in bsp_lvgl_port.cpp. Maintain a small independent brightness state and update it in the existing LVGL task under its mutex. Report touch activity in the common touch reader, and button activity from the existing GPIO/PMU polling paths under the same mutex. Write panel brightness only when the value changes. Use unsigned elapsed time to tolerate tick rollover.

Alternatives: page-specific timers duplicate behavior and can miss overlays; an opacity overlay changes rendering and can interfere with input. The shared hardware controller covers all pages without changing UI objects.

## Constraints
No screen sleep, animation pause, timer changes, page changes, or input consumption. Incoming status updates and autonomous animations do not count as user input. Keep existing button debounce, long press, repeat, and power overlay semantics. No new blocking delay or task.

## Files and validation
Modify firmware/desk-mate-app/bsp_lvgl_port.cpp and .h for shared activity reporting and brightness updates, and desk-mate-app.ino for physical inputs. Add a small portable state helper and focused native checks for the 10-second boundary, fade, interruption/recovery, held input, and tick rollover. Compile the ESP32-C6 firmware. Hardware acceptance checks all three pages with active animations and touch/button recovery; flashing is outside this change unless requested.
