# BLE voice integration R1

User approved proceeding from the measured BLE prototype to implementation. Real speech, recognition and software-control acceptance is performed by the user. Automated verification uses protocol fixtures, synthetic PCM, fake adapters and local test state only.

## Behavior

Default voice transport is BLE. Page 2 HID and page 3 voice use the single selected encrypted/bonded BLE connection, never two connections. Wi-Fi/WSS is retained as an explicit selectable mode; no automatic failover or uncertain-command replay. The 16kHz mono s16 stream, short-press qualification, live RMS UI, cancel window, seal/commit/status/replay/feedback and durable pending-command ownership remain unchanged. Phone/tablet media support does not imply an authorized voice companion exists.

Page 3 management exposes BLE / Wi-Fi mode. BLE computer selection routes through the existing Bluetooth owner using a persisted authenticated peer/server binding; an unbound computer instructs the user to connect the Mac in Bluetooth and start its bridge. Existing USB-authorized computer profiles supply credentials. Wi-Fi retains its remembered target independently of transient BLE selection. Active/uncertain voice work prevents target switching. Disconnecting before commit cancels staging; committed work is queried only on its original server.

## Wire and ownership

Production GATT service d35c1001-69b0-4b89-9a72-5fd60d200001; RX=1002 (encrypted write), TX=1003 (notify), status=1004 (encrypted read). Status carries version, flags, MTU, random connection epoch, BLE generation and maximum message size. This is separate from the opt-in laboratory service. HID service registration order remains stable; signal GATT service changes to existing peers. A stale OS cache is reported without clearing bonds automatically.

Both directions carry bounded fragmented messages. A 16-byte little-endian header contains version u8, kind u8 (JSON=1, audio=2, cumulative credit ACK=3), epoch u32, packet sequence u32, offset u16, message total u16 and payload length u16. Packets use negotiated MTU, capped at 244 bytes. Messages <=2048 bytes, one contiguous partial message per direction, strict sequence/offset/length checks, cumulative 24-packet receive credit, bounded queues and 2s stalled-ACK timeout. ACK packets do not consume data credit. All radio operations remain in the existing BLE manager owner; callbacks enqueue generation-bound packets. The voice transport task owns application messages and credentials. A new epoch fences all queued data on link/target/mode changes.

PCM remains the existing binary message (8-byte frame header + up to640 bytes). Preserve the original staged audio hash and frame numbering above fragmentation. Apply backpressure before popping the audio ring; control traffic uses the same ordered bounded message stream. Tune interval/PHY during active audio then restore idle interval. No microphone capture occurs in automated transport tests.

## Authentication

BLE bonding alone does not authorize software control. Reuse the existing USB-provisioned per-device credential. A symmetric BLE key is SHA256(token), equal to the private server database's existing token_hash decoded as bytes; no raw token crosses GATT. The DB remains sensitive credential material. Mutual HMAC-SHA256 proofs bind protocol label, server_id, device_id, fresh 32-byte device and Mac nonces and the current BLE epoch. Distinct server/device/complete labels prevent reflection. The device chooses only an existing USB profile matching the claimed server. Complete proof expires within10s and is single-use. Compare proofs in constant time. Persist peer address/type -> server_id only after both proofs complete; reject silent re-binding to another server. Forgetting a Bluetooth peer removes its binding. Revoked/rotated server credentials stop an existing companion session.

After authentication the firmware sends the same hello and control/audio messages used by WSS. The Mac BLE central feeds a shared authenticated remote-session dispatcher. Extract the existing WSS message handler once rather than duplicating cancellation, commit, feedback or execution logic. Reject cross-device status/cancel/seal/commit access. Sender-side BLE identity is not selected by friendly-name guessing.

## Mac bridge and evolution

Run the existing local assistant with an explicit --ble-address CoreBluetooth UUID. Its BLE connector and WSS endpoint share session/storage/planning/executor infrastructure and one connection manager per authenticated device. BLE reconnect does not automatically re-execute commands. A synthetic verification mode can authenticate and send/cancel staged audio without invoking STT or applications; fixtures use isolated test databases. The future instruction engine remains transport/session -> STT/planner -> platform/application adapters -> records/feedback/evaluated optimization. This task does not add unrestricted phone automation or a new GUI.

## Verification

Portable C/Python golden framing/auth vectors; credit exhaustion/backpressure, invalid lengths, stale epochs/sequences, wrong peer/proofs/expired auth, queue limits and disconnect recovery. Shared session fake-STT/fake-adapter regression for WSS and BLE-shaped channels, especially cancel-before-commit and cross-device ownership. LVGL8/9 and32KB UI pool, host unit tests, normal firmware compile/size, optional synthetic BLE checks only. Flash verified production build for user speech acceptance; no real microphone/recognition/software-control test by the agent.
