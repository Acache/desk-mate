# BLE voice bridge — first experiment and evolution

## User-authorized direction

Retain Wi-Fi, first run a small BLE experiment, share a single physical BLE connection between page 2 media shortcuts and page 3 general voice. The peer-side program is the long-lived receiver, manager, executor, recorder and optimization engine, eventually adaptable to PC/phone/tablet. This is approval of the staged direction, not a request to immediately implement every future platform.

## First experiment

Add an opt-in DESKMATE_BLE_LAB service to the existing NimBLE server. Do not create another server, connection or bond database. Production firmware defaults to the existing Wi-Fi voice path. The laboratory stream uses deterministic, verifiable synthetic bytes at the same rate as PCM, initially 32,000 B/s, then 50,000 B/s for headroom. No microphone or application execution is involved. Thus real transport measurements are permitted, while the user's earlier no end-to-end application test boundary remains intact.

Alternatives considered: replacing WSS immediately risks control/cancel/recovery regressions before proving throughput; compressed audio first obscures transport capacity and adds codec CPU/accuracy questions. Measure raw transport first, then choose raw PCM or a codec from evidence.

The lab is available only over the one selected, encrypted, bonded, HID-ready connection. Control writes are queued with handle/generation; notifications require a subscription from that same connection. Target changes, disconnects, paused Bluetooth, lost subscription, queue overflow or acknowledgment stalls stop the experiment. Real voice activity blocks starting a run; the run owns the voice input gate until it stops. No app authorization token is exposed through the lab. A fixed 16-byte control record starts/stops and cumulatively acknowledges data. Notifications have a 16-byte version/run/sequence/offset/length header, payload split according to negotiated MTU and a bounded receiver-credit window (24 packets by default). The lab requests a 15ms connection interval and 2M PHY for the run, restoring the normal 15–30ms interval afterwards. Peer acceptance is measured rather than assumed. A read-only link characteristic reports actual peer count/MTU/interval/generation. Sender advances only on successful notify; receiver validates every byte and acknowledges progress. Run IDs must increase within a connection; stale control requests cannot start another run. Maximum run 60 seconds / 3.2MB; rates 1,000–80,000 B/s. Stall limit 2 seconds, overall deadline duration + 5 seconds. One service with control READ/WRITE and data NOTIFY, no extra connection. Source-built NimBLE CCCD store budget grows to 32 for eight bonds with HID/battery/service-change/lab subscription metadata.

Mac experiment tool has optional Bleak dependency, explicit scan/connect command, bounded receive/ack queues, integrity checks, elapsed throughput and longest receive gap, JSON report. It never initializes STT, the action pipeline, real command database or software adapters. A candidate pass requires exact total bytes, no corruption/gap, firmware COMPLETE with full receiver acknowledgment, at least 95% of target useful throughput and no receive pause over 250ms. This does not qualify production voice by itself; sustained, busy-radio, cancel, sleep/wake and media coexistence still need acceptance. No synthetic HID media keys are sent automatically.

## Shared product semantics after the experiment

One selected Bluetooth peer is the target of both pages. Page 2 exposes OS-supported HID shortcuts; page 3 requires an authenticated bridge service on that same peer. A bonded phone without a companion bridge remains media-capable but not voice-capable. Media/voice capabilities are negotiated separately from online/authorized state. A saved BLE identity must be explicitly bound to the bridge's stable server identity; device names or MAC-to-name guesses do not establish trust. Switching the shared target ends an uncommitted voice session first; an already committed command remains attributable to its originating peer. Wi-Fi retains its explicit endpoint and is a fallback transport, never an automatic reroute/replay of an uncertain command to a different device.

Keep begin/audio/seal/commit/cancel/status/idempotency semantics above transport. BLE fragmentation and acknowledgments are transport responsibilities. Disconnection must never commit a staged command. Existing Wi-Fi certificate/token trust does not magically transfer through BLE bonding: production BLE must include mutual application authentication with fresh challenges, credential provisioning and revocation before enabling actions. Do not disable WSS certificate checks as a shortcut.

## Peer-side bridge architecture

1. Transport adapters (BLE now experimental; WSS retained; future transports optional) normalize authenticated sessions and framed audio/control.
2. Session/application layer owns authorization, peer capability registry, command ownership, cancel/commit and durable idempotency, independent of transport.
3. Existing STT/planner/pipeline/executor remain reusable; avoid copying the large WebSocket handler into another handler. Extract the shared message dispatcher only when adding actual BLE commands, with WSS regression coverage.
4. Platform/application adapters declare supported operations and verify results. Current macOS/Bilibili support is real; Windows/iOS/Android/iPadOS capabilities remain conditional on companion software and OS permissions. No promise of unrestricted phone-wide automation.
5. Records/feedback/personalization remain local, attributable to peer, app, command and model/policy version. Learning improves recognition and planning through evaluated updates; it must not silently broaden execution permissions.

Longer term the peer application can be presented as a device bridge / instruction engine, with UI for connection health, capabilities, command history, feedback and evaluated optimization. This round documents the boundary and builds the transport laboratory; it does not redesign or replace the existing control application.

## Verification

Native protocol tests for byte framing, credit boundaries, exact final short packet, stale generations/runs, timeout rollover and cap limits. Python unit tests for malformed/stale/duplicate data, integrity, bounded queues, validation thresholds and fake BLE client full transfer without external applications. Full existing host unit and firmware/simulator checks. Compile normal and lab firmware, ensure normal has no lab service. Flash lab only after validation; attempt local BLE data-only measurements if OS permissions and selected peer allow. Report hardware limitations honestly without substituting mock throughput.

## Discovery-cache compatibility

If macOS retains the old GATT service table and cannot see the additive optional info characteristic, data-rate qualification continues over the original control/data characteristics without erasing bonds. Report missing firmware peer-count/generation diagnostics explicitly; uninterrupted same-client connection lifetime is tracked separately. Invalid info that actually reports multiple peers remains a hard failure.
