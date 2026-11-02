# Global PWR hold feedback

User authorized implementation and adjustment of interaction details.

## Goal and timing

AXP2101 explicitly uses hardware long-press power-off (not restart), 4 seconds.
Power-on keeps its existing hardware setting: the PMIC only offers 128ms/512ms/1s/2s,
so a true 4s cold power-on and a display while unpowered are outside firmware control.

Short taps retain page-owned actions. After 1s continuously held, show a global
floating card with a circular progress indicator and elapsed seconds 0s–3s.
The center uses 48px integer labels 0s/1s/2s/3s. Arc updates remain continuous
at 20ms cadence, independent of integer text changes. The card says HOLD 3s TO POWER OFF and RELEASE TO CANCEL, matching the remaining
three seconds. Fonts follow the existing English firmware UI.

On release, never route a cancelled hold as a tap. Reverse from the exact displayed
progress, with duration proportional to progress and capped at 2s, then fade the
whole overlay for 1s. A fresh press discards old progress and starts a new hold.
The global top layer blocks touch through the overlay and sits above BLE/voice UI.
PMIC hardware remains responsible for actual shutdown, even if UI stalls.

## Architecture and validation

Use a portable power-hold model, one coherent PMIC edge/short IRQ snapshot per poll,
and a shared LVGL 8/9 overlay. Allocate overlay only when needed, delete after fade.
Test timing boundaries, release/cancel, re-press, delayed short IRQ, time wrap,
and LVGL visibility/arc/opacity on both simulator versions including hardware pool.
Compile actual ESP32-C6 firmware. Physical power-cut and power-on require device QA.
