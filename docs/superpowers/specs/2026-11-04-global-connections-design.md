# Global connection drawer — proposal for user review

## Goal
Move connection configuration out of the Media and Voice working pages into one discoverable global entry. Keep media playback/volume and voice recording/cancel as immediate page actions. Main pages show current target, transport, readiness, latest event and actionable error.

## Entry and navigation
Top-edge pull-down (gesture must start in a narrow top strip, so timer wheel drags and horizontal paging keep their ownership). Also provide a small visible connection/status affordance for discoverability and physical-button access. Drawer uses an opaque surface and direct show/hide initially; no expensive full-screen opacity animation. Back returns one level, root Back or upward dismissal returns to the exact original page/scroll position. PWR hold overlay stays above it.

## Root: Connections
Three rows/cards, with consistent list/detail/back patterns matching page-2 management:
1. Bluetooth: current peer, connection state, saved devices, pair/switch/rename/forget, enable/auto-reconnect.
2. Wi-Fi: SSID/connection state, setup/change network, reconnect, explicit connection progress/failure/retry.
3. Voice connection: Bluetooth or Wi-Fi transport selection with a plain explanation; Wi-Fi computer authorizations/default computer belong here. Bluetooth voice follows the selected Bluetooth peer. Do not imply the two registries are interchangeable or create a fake global target switch.
Use explicit current/default labels rather than unexplained > and * markers. Wi-Fi setup is distinct from choosing voice transport; connected Wi-Fi does not mean voice is ready.

## Behavior
Browse status while a voice command runs, but block disruptive transport/device changes with a clear busy explanation. Preserve existing switch confirmation, failed-switch recovery, remove confirmation, trust and credential handling, network setup lifecycle, and physical-button ownership. No duplicate device registries. Maintain low-memory destruction/rebuild behavior and refresh data before first visible frame.

## Alternatives
A. Recommended: global drawer plus simplified page status entries, one configuration hierarchy.
B. Smaller scope: keep separate Media/Voice managers, but give both consistent list/detail/back structure and clarify Network versus Voice transport. Less implementation work, still multiple entry locations.

## Validation
Native model/UI lifecycle, edge-gesture conflicts, physical navigation, back stack and exact return-frame tests in LVGL8/9 and 32KB pool; connected/disconnected/recording/busy/error states; screenshot review before hardware acceptance. User approved the global drawer on 2026-11-04; implemented for hardware acceptance.

## Implementation constraint

The existing controller gate refuses management entry during an active voice command or while its key is held. Keep that ownership rule and show VOICE BUSY; read-only browsing during commands is deferred. The opaque drawer uses no group-opacity animation. See docs/CONNECTIONS_B_VALIDATION.md.
