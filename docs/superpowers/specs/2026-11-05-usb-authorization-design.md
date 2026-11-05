# USB authorization without device reset

## Goal and authorization
The user approved the previously proposed direction: verify the reset from logs, then distinguish normal connection from USB add/re-authorize, preserve device UI/Bluetooth state and test it. No additional design confirmation is needed for that accepted scope.

## Observed cause
- The current production pySerial open sequence (DTR=false, RTS=false set before open) produces a fresh `rst:0x15 (USB_UART_HPSYS)` before any authorization command, followed by boot initialization and lost authentication.
- Pure POSIX `os.open` and close preserve authentication. Atomic TIOCMGET/TIOCMSET updating DTR+RTS also preserves authentication through open, VA_SETUP acknowledgement and close. The initial test restored only the previously selected Bluetooth target; no credentials were changed.
- Evidence: build/usb-open-diagnostic and build/usb-atomic-diagnostic. Do not disable the firmware USB reset hardware or alter automatic Bluetooth reconnection preferences.

## Implementation
1. Use atomic modem-line updates on POSIX for the identified Espressif native USB Serial/JTAG port (VID303A/PID1001). Preserve unrelated modem bits; unsupported safe control must fail clearly, without falling back to the resetting sequence. Other port/platform paths remain compatible.
2. Normal connection uses an explicit reconnect operation that replaces only the BLE connector. Service/STT/Ollama stay alive. Block connection changes during active voice commands.
3. Main UI distinguishes select/reconnect/add-device/disconnect; explicit USB reauthorization lives in advanced controls. Timeout/offline means authorization unconfirmed, not unauthorized. Only actual authenticated health proves current authorization.
4. Remember successful USB grants by stable USB hardware identity in the private application database, without storing plaintext tokens or hardware serial numbers. The registration mapping, new device and old-credential revocation commit in one SQLite transaction. Adding the same known valid local registration returns its historical status without opening the serial port or rotating credentials. This is not proof of current device authorization. A different USB device remains addable. Explicit reauthorization repairs cleared board credentials; only the mapped old credential is replaced after device acknowledgement. Preserve other computers and Wi-Fi configuration. A failure to save the optional diagnostic summary must not turn a durable successful grant into a reported failure. If the board ACKed but the database transaction fails, explain the partial outcome and explicit repair path.
5. Real acceptance exposed retained wire counters after a new notification subscription and an older peer-to-server binding. A fresh subscription starts a new voice session; same-generation rearming must not reset it. While idle, the Mac observes firmware epoch changes and refreshes authentication without restarting service/models or doing extra STATUS reads during audio commands.
6. A successful USB profile write records a one-use binding migration grant for that profile. Only a complete cryptographic proof matching its current device/server/token may consume it and update that peer binding. Reject stale or incomplete proofs, invalid peers and unrelated conflicts. Keep other profiles, bonds and Wi-Fi intact. This is a scoped firmware session/binding fix, not a global USB reset workaround.

## Validation
- Regressions cover atomic line updates/preserved bits/failure/port selection; repeated authorization no serial access/no token rotation; distinct devices; revoked registration; explicit replacement; acknowledgement failure.
- Reconnect replaces the connector for the same selected device, respects active-command gates, preserves model/service state and rejects malformed local control messages.
- Actual board checks: repeated safe open/handshake/close produces no boot banner or physical BLE loss; one real USB grant and subsequent idempotent repeat, credentials persistence and true reconnect; original automatic-reconnect preference unchanged.
- Publish only in build/usability-release/Desk-Mate.app using the fixed local signing identity and incremented version/build. Check native permissions retained. Do not commit before user acceptance.
- Completed on 0.3.2 build 14 with 284 Python unit tests, 8 protocol integration tests, 6 relevant native firmware tests and the full single-board hardware flow. See ../../USB_AUTHORIZATION_VALIDATION_2026-11-05.md for evidence and coverage limits.
