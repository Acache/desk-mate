#!/usr/bin/env python3
"""Explicit hardware acceptance against the published app and selected ESP32.

Without --authorize this only inspects state. --authorize permits credential
creation, --replace additionally tests deliberate replacement, and --reconnect
tests the actual running service's reconnect endpoint. Never prints tokens.
"""
import argparse
import json
import plistlib
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--serial-port', required=True)
    parser.add_argument('--authorize', action='store_true')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--reconnect', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if (args.replace or args.reconnect) and not args.authorize:
        parser.error('--replace/--reconnect require --authorize')
    resources = args.app.resolve() / 'Contents/Resources'
    sys.path.insert(0, str(resources / 'mac/src'))
    from voice_assistant.config import load_config
    from voice_assistant.storage import Database
    from voice_assistant import provisioning as provision
    from voice_assistant.desktop_control import request_reconnect
    from voice_assistant.usb_serial import open_provisioning_port

    info = plistlib.loads((args.app / 'Contents/Info.plist').read_bytes())
    report = {'version': info['CFBundleShortVersionString'], 'build': info['CFBundleVersion'],
              'actual_hardware': bool(args.authorize), 'steps': [], 'accepted': False}
    db = None
    serial_lines = []

    def sample():
        state = json.loads((args.root / 'desktop-state.json').read_text())
        health = state.get('health', {})
        ble = health.get('ble', {})
        return {'phase': ble.get('phase'), 'authenticated': ble.get('authenticated'),
                'device_id': ble.get('device_id'), 'generation': ble.get('generation'),
                'epoch': ble.get('epoch'), 'updated_at': state.get('updated_at', 0),
                'error_code': ble.get('error_code'), 'pids': state.get('pids', {}),
                'stt_pid': health.get('stt', {}).get('pid'), 'ready': health.get('ready')}

    def rows():
        # Keep credential hashes in memory only, for unchanged-data assertions.
        with db.transaction() as tx:
            return {row['id']: dict(row) for row in tx.execute(
                'SELECT id,token_hash,revoked_at,display_name FROM devices').fetchall()}

    def wait_authenticated(device_id, not_before=0):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            value = sample()
            if (value['authenticated'] and value['device_id'] == device_id
                    and value['updated_at'] >= not_before):
                assert value['pids'] == baseline['pids'], 'service/model restarted'
                assert value['stt_pid'] == baseline['stt_pid'], 'STT restarted'
                return value
            time.sleep(.5)
        raise RuntimeError('Current service did not authenticate the written device credential')

    original_factory = provision.open_provisioning_port

    def actual_pair(force=False):
        openings = []

        def trace_factory(name):
            port = original_factory(name)
            openings.append(name)
            read = port.readline
            reset = port.reset_input_buffer

            def record(line):
                if line.startswith(('ESP-ROM:', 'rst:', 'READY heap=', 'VA_OK ', 'VA_ERR ',
                                    'STATUS ', 'BTM ', 'VA: config saved')):
                    serial_lines.append(line)

            def trace_reset():
                # Capture boot output that ordinary provisioning would discard
                # before VA_SETUP, without changing any modem-control signals.
                waiting = port.in_waiting
                if waiting:
                    for line in port.read(waiting).decode('utf-8', 'replace').splitlines():
                        record(line.strip())
                return reset()

            def trace_read(*a, **kw):
                data = read(*a, **kw)
                line = data.decode('utf-8', 'replace').strip()
                record(line)
                return data

            port.readline = trace_read
            port.reset_input_buffer = trace_reset
            return port

        provision.open_provisioning_port = trace_factory
        try:
            result = provision.pair_device(cfg, db, args.serial_port, '', '', force=force)
        finally:
            provision.open_provisioning_port = original_factory
        entry = {'operation': 'replace' if force else 'add', 'unchanged': result['unchanged'],
                 'serial_opens': len(openings), 'device_id': result['device_id']}
        report['steps'].append(entry)
        live = wait_authenticated(result['device_id'])
        assert live['generation'] == baseline['generation'], 'USB authorization dropped the physical Bluetooth link'
        entry['actual_service'] = live
        print(json.dumps(report['steps'][-1], ensure_ascii=False), flush=True)
        return result

    def idempotent(device_id):
        before = rows()
        build, send = provision.build_bundle, provision.send_bundle

        def forbidden(*a, **kw):
            raise AssertionError('Known USB registration attempted credential generation or serial I/O')

        provision.build_bundle = provision.send_bundle = forbidden
        try:
            result = provision.pair_device(cfg, db, args.serial_port, '', '')
        finally:
            provision.build_bundle, provision.send_bundle = build, send
        assert result['unchanged'] and not result['authorization_verified']
        assert result['device_id'] == device_id and before == rows()
        report['steps'].append({'operation': 'repeat', 'unchanged': True,
                                'serial_opens': 0, 'credentials_unchanged': True})

    try:
        baseline = sample()
        report['baseline'] = baseline
        if not args.authorize:
            report['executed'] = False
            return
        assert baseline['authenticated'] and baseline['ready'], 'Start with the selected board connected and service ready'
        cfg = load_config(args.root)
        state = json.loads((args.root / 'desktop-state.json').read_text())
        cfg.port = state['port']
        db = Database(cfg.db_path)
        first = actual_pair()
        idempotent(first['device_id'])
        current = first
        if args.replace:
            before = rows()
            current = actual_pair(force=True)
            assert current['device_id'] != first['device_id']
            after = rows()
            assert after[first['device_id']]['revoked_at'] is not None
            assert all(after[key] == value for key, value in before.items() if key != first['device_id'])
            assert len(after) == len(before) + 1
            report['only_mapped_credential_replaced'] = True
            idempotent(current['device_id'])
        if args.reconnect:
            credentials_before_reconnect = rows()
            previous_epoch = sample()['epoch']
            requested_at = time.time()
            result = request_reconnect(args.root)
            assert result['ok'], result
            live = wait_authenticated(current['device_id'], not_before=requested_at)
            assert live['epoch'] != previous_epoch, 'Reconnect did not establish a fresh voice session'
            assert rows() == credentials_before_reconnect, 'Reconnect changed device credentials'
            report['steps'].append({'operation': 'reconnect', 'code': result['code'],
                                    'actual_service': live, 'models_preserved': True,
                                    'credentials_unchanged': True})
        # Observe the same running board after authorization, without setup or
        # write commands. Initial READY/ROM banners are evidence of a reset.
        port = open_provisioning_port(args.serial_port)
        deadline = time.monotonic() + 12
        try:
            while time.monotonic() < deadline:
                line = port.readline().decode('utf-8', 'replace').strip()
                if line.startswith(('ESP-ROM:', 'rst:', 'READY heap=', 'STATUS ', 'BTM ')):
                    serial_lines.append(line)
        finally:
            port.close()
        assert not any(line.startswith(('ESP-ROM:', 'rst:', 'READY heap=')) for line in serial_lines), 'ESP32 reset during USB operations'
        assert any(line.startswith('STATUS ') for line in serial_lines), 'Missing actual board status'
        report['serial_evidence'] = serial_lines
        # The desktop health file refreshes asynchronously. Require two fresh
        # observations after close rather than accepting a cached pre-close UI.
        closed_at = time.time()
        report['post_close_first'] = wait_authenticated(current['device_id'], not_before=closed_at)
        report['final'] = wait_authenticated(current['device_id'], not_before=time.time() + 3)
        report['executed'] = report['accepted'] = True
    except BaseException as exc:
        report['error'] = str(exc)
        report['serial_evidence'] = serial_lines
        raise
    finally:
        if db is not None:
            db.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'accepted': report['accepted'], 'report': str(args.report),
                          'error': report.get('error')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
