# Refined drawer, page layout and desktop UI

User specified the device design and approved the coordinated dark desktop-card design.

- Remove the large duplicate timer ring from Media; show compact Focus time instead. Make device/transport status plain noninteractive text.
- Replace drawer entry/exit text and Close button with drawn flat chevrons. Move opaque drawer with finger; release past halfway to commit, otherwise settle back. Preserve busy gate, physical controls and page restoration.
- Desktop: persistent Connection/Service-and-Voice/Permissions cards, matching dark/slate/warm-white/muted-blue palette; advanced actions in disclosure section. Update existing labels instead of rebuilding the whole window every poll.
- Remove automatic modal result/failure dialogs. Quiet startup/offline waiting, one notification on genuine connection loss, explicit connection-attempt errors and service faults. Deduplicate/restrict notifications; if denied, leave status visible without modal fallback. Explicit device pickers and destructive confirmations remain dialogs.
- Distinguish device discovery, connection, channel setup and authentication timeouts in backend error codes.

Validation: LVGL8/9 gestures (partial/full/reversed drags), noninteractive status, timer layout, exact return pages and 32KB pool; Swift compile and notification-policy self-check; backend control/error tests; desktop offscreen visual render; bundle signing; firmware upload. App native UI automation timed out, so do not claim interactive desktop acceptance.
