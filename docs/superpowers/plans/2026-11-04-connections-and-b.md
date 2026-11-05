# Global Connections and B display trial

User approved global connection drawer and authorized B experiments; combine with first-frame peer-name fix for hardware acceptance.

- [x] Implement shared top-edge/tap entry, opaque root with Bluetooth/Wi-Fi/Voice branches, consistent back navigation and original-page restoration.
- [x] Reuse existing registries and command queues; explain transport separately from Wi-Fi connectivity; use Current/Default labels.
- [x] Integrate physical key ownership and management gate, power-overlay priority and low-memory view lifecycle.
- [x] Populate media labels during reconstruction, before returning its first frame.
- [x] Benchmark 10/12/20-line buffers and 4-slot circle cache. Reject 4 slots after 32KB-pool OOM; choose conservative 12-line trial (+3,840 bytes DMA RAM), preserve 2 slots and refresh/input periods.
- [x] Test root and children on all three pages, return stack, top-edge gesture, busy-state gate, power overlay, 32KB pool and existing LVGL8/9 regressions.
- [x] Firmware compile, upload and boot health check passed.
- [ ] User hardware acceptance; no Git commit yet.

## Scope clarification
Existing voice management gate refuses entry while a command is active or a physical recording key is held. Preserve it and show VOICE BUSY instead of introducing new input races in this revision. Thus the proposed read-only browsing while recording is deferred; users return to the command page for live status/cancel. Root configuration opens when idle. No group-opacity fade; fast opaque show/hide.
