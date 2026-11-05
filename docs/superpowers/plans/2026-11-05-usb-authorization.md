# USB authorization implementation plan

**Goal:** Remove the confirmed USB control-line reset and separate daily reconnection from credential creation.

**Architecture:** Preserve USB bootloader/reset behavior. Add a narrowly selected atomic POSIX serial adapter, transactional private USB-registration history in SQLite, and a local BLE reconnect operation surfaced by the native App. Hardware acceptance also required fresh BLE subscription sessions and a narrowly scoped, one-use USB-authorized legacy binding migration in firmware.

**Tech Stack:** Python/pySerial, POSIX termios/ioctl, SQLite, Swift AppKit, existing BLE connector.

## Constraints
- Follow approved design in ../specs/2026-11-05-usb-authorization-design.md.
- Keep canonical app path and persistent local signing identity.
- No global reset-bit disable, TCC changes, automatic-reconnect preference changes, plaintext token cache, or mistaken global block on adding a second board.

## Tasks
- [x] Add failing serial-control regressions; implement usb_serial.py factory with atomic DTR/RTS for the identified native USB adapter.
- [x] Add real SQLite provisioning tests for idempotent USB identity history and explicit replacement; use the safe serial factory. Verify schema 2 → 3 preserves all prior tables and failed COMMIT rolls back without leaving the connection stuck.
- [x] Add connector/reconnect protocol tests; implement reconnect without stopping service/models.
- [x] Update native UI and CLI responses to distinguish add, reconnect, historical registration and explicit reauthorization; compile Swift.
- [x] Review changes and run relevant unit/protocol checks.
- [x] Fix and test fresh BLE subscription epochs, idle credential refresh, and legacy peer binding migration only after a full proof using the current USB-written profile. Preserve other profiles and Wi-Fi; never clear all bindings.
- [x] Exercise actual board safe handshake, USB provision/repeat/reconnect and preserve baseline settings; save sanitized evidence in build/usb-authorization-acceptance.json. Scope: one ESP32 and this Mac, not physical switching among multiple computers or a complete Wi-Fi flow.
- [x] Publish 0.3.2 build 14 in the original app path, verify permission retention and ready state, update docs. Leave git uncommitted for user verification.
