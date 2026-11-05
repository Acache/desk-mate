# Desk-Mate project conventions

## Local macOS release and verification
- The only local published app path is `build/usability-release/Desk-Mate.app`. Never move it into date-stamped directories or create alternative installed app copies for testing.
- Use `mac/scripts/update_local_app.py` to stage, validate and replace that app in place. It keeps user Application Support data and retires the previous dated duplicate. Version source: `mac/app/version.json`; increment build number for each publication.
- Local updates use the persistent certificate in `~/Library/Application Support/desk-mate/signing` (`--identity local`). Reuse it; never recreate or rotate it during routine builds. It is private signing state, excluded from source, bundles and diagnostic exports. Do not modify system trust or the keychain search list. Full local builds must also pass `--identity local`.
- Never execute a staged `.app`, including self-checks. Publish to the canonical path before running native checks; staging must remain data only.
- Read Accessibility/Screen Capture authorization in the running main App process. A command-line child has a different TCC responsibility context and must not supply the desktop UI's permission state. Explicit recheck uses the native request APIs; ordinary refresh uses native preflight APIs without prompting or restarting models.
- Do not reset TCC/privacy grants as part of publishing or ordinary rechecking. Without a stable signing identity, do not promise ad-hoc updates preserve all grants or weaken designated requirements to simulate that. Old System Settings rows can belong to an earlier code identity; never label a new executable authorized merely because that old row is checked.
- Desktop is a regular Dock/Command-Tab app; show the main window on launch/reopen. Normal background waiting/retry must not raise modal alerts.

## Voice context semantics
- Resolve video ordinals/titles from the current visible page: home cards, search results, player episodes/recommendations. Prior search is not a universal prerequisite.
- Only exact, complete simple commands take deterministic routes. Composite/conditional unfamiliar requests retain current page/player context and go to the model. Do not execute a single sub-clause because a regex happened to match it.
- Preserve current page context across player refresh. Prefer focused/front window over stale search targets. Bind targets to observed stable IDs; reject invented/ambiguous targets rather than pretending to execute.
- All model proposals still pass typed contracts, policy guards, planner binding, cancellation and effect verification. Do not treat a proposal or a subprocess return code as verified product success.

## ESP32 USB authorization
- For the ESP32-C6 native USB Serial/JTAG port, use `voice_assistant.usb_serial.open_provisioning_port` for provisioning and diagnostics. Setting DTR/RTS false before ordinary pySerial open still resets this board; the atomic modem-line path has been verified on hardware. Do not globally disable the firmware USB reset mechanism or alter Bluetooth auto-reconnect preferences to hide the issue.
- Daily reconnect must not open USB or rotate credentials. Bind registration history to stable USB hardware identity plus server ID, and persist it with credential changes in the same database transaction. History alone is not proof of live authorization; only authenticated health verifies the current connection.
- Keep explicit USB reauthorization separate from adding a board. Preserve other computers, Wi-Fi and Bluetooth bonds. Never log plaintext device tokens or their directly usable BLE authentication key.
- A new voice notification subscription needs fresh wire counters/epoch; rearming an existing subscription must preserve its session. Observe idle epoch changes without extra STATUS reads during active audio commands.
- Legacy peer binding migration requires a one-use grant from a successful USB profile write plus full proof matching the current device/server/token. Consume only that profile's grant; never clear all bindings or relax authentication to repair one peer.
