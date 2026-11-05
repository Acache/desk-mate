"""Safe native-USB modem control without opening any actual serial device."""
import errno
import re
from types import SimpleNamespace

import pytest
import serial

from voice_assistant import usb_serial as usb


def port(device="/dev/cu.test", *, vid=0x303A, pid=0x1001, serial_number="board-A"):
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial_number)


def test_hardware_identity_survives_port_rename_and_never_opens(monkeypatch):
    listing = [port()]
    monkeypatch.setattr(usb.list_ports, "comports", lambda: listing)
    monkeypatch.setattr(serial, "Serial", lambda *a, **kw: pytest.fail("identity lookup must not open USB"))
    original = usb.usb_port_identity("/dev/cu.test")
    listing[:] = [port("/dev/cu.changed")]
    assert usb.usb_port_identity("/dev/cu.changed") == original
    assert re.fullmatch(r"[a-f0-9]{64}", original)
    assert "board-A" not in original
    listing[:] = [port("/dev/cu.changed", serial_number="board-B")]
    assert usb.usb_port_identity("/dev/cu.changed") != original
    listing[:] = [port("/dev/cu.changed", vid=0x1234)]
    assert usb.usb_port_identity("/dev/cu.changed") != original


@pytest.mark.parametrize("info", [port(serial_number=None), port(serial_number=""), port(vid=None), port(pid=None)])
def test_incomplete_hardware_identity_is_not_guessed(monkeypatch, info):
    monkeypatch.setattr(usb.list_ports, "comports", lambda: [info])
    assert usb.usb_port_identity(info.device) is None
    assert usb.usb_port_identity("/dev/missing") is None


def test_macos_call_in_and_call_out_names_identify_same_device(monkeypatch):
    monkeypatch.setattr(usb.sys, "platform", "darwin")
    monkeypatch.setattr(usb.list_ports, "comports", lambda: [port()])
    assert usb.usb_port_identity("/dev/tty.test") == usb.usb_port_identity("/dev/cu.test")


def test_native_usb_factory_configures_safe_class_before_open(monkeypatch):
    monkeypatch.setattr(usb.os, "name", "posix")
    monkeypatch.setattr(usb.list_ports, "comports", lambda: [port()])
    opened = []
    def record_open(self):
        opened.append(self)
        assert self.port == "/dev/cu.test"
        assert self.baudrate == 115200
        assert self.timeout == .3 and self.write_timeout == 5
        assert self.dtr is False and self.rts is False
        assert self.dsrdtr is False and self.rtscts is False
    monkeypatch.setattr(usb._AtomicSerial, "open", record_open)
    result = usb.open_provisioning_port("/dev/cu.test")
    assert type(result) is usb._AtomicSerial and opened == [result]


@pytest.mark.parametrize("vid,pid", [(0x10C4, 0xEA60), (0x303A, 0x9999), (None, None)])
def test_other_usb_ports_keep_standard_serial_path(monkeypatch, vid, pid):
    monkeypatch.setattr(usb.list_ports, "comports", lambda: [port(vid=vid, pid=pid)])
    opened = []
    monkeypatch.setattr(serial.Serial, "open", lambda self: opened.append(self))
    result = usb.open_provisioning_port("/dev/cu.test")
    assert type(result) is serial.Serial and opened == [result]


def test_non_posix_keeps_standard_native_usb_path(monkeypatch):
    monkeypatch.setattr(usb, "_POSIX", False)
    monkeypatch.setattr(usb.list_ports, "comports", lambda: [port()])
    opened = []
    monkeypatch.setattr(serial.Serial, "open", lambda self: opened.append(self))
    result = usb.open_provisioning_port("/dev/cu.test")
    assert type(result) is serial.Serial and opened == [result]


def test_failed_metadata_scan_never_opens_an_unidentified_port(monkeypatch):
    def failed_scan():
        raise OSError("USB metadata unavailable")
    monkeypatch.setattr(usb.list_ports, "comports", failed_scan)
    monkeypatch.setattr(serial.Serial, "open", lambda *a: pytest.fail("must not open after failed identification"))
    assert usb.usb_port_identity("/dev/cu.test") is None
    with pytest.raises(serial.SerialException, match="identify"):
        usb.open_provisioning_port("/dev/cu.test")


@pytest.mark.parametrize("dtr,rts", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("update", ["_update_dtr_state", "_update_rts_state"])
def test_each_control_update_changes_both_lines_together_and_preserves_other_bits(monkeypatch, dtr, rts, update):
    if usb.termios is None:
        pytest.skip("POSIX test")
    obj = usb._AtomicSerial(None)
    obj.fd = 12345
    obj._dtr_state, obj._rts_state = dtr, rts
    old = usb.termios.TIOCM_DTR | usb.termios.TIOCM_RTS | 0x40000000
    calls = []
    def ioctl(fd, operation, buffer, *args):
        assert fd == 12345
        calls.append((operation, args))
        if operation == usb.termios.TIOCMGET:
            buffer[0] = old
        elif operation == usb.termios.TIOCMSET:
            desired = buffer[0]
            assert desired & 0x40000000
            assert bool(desired & usb.termios.TIOCM_DTR) is dtr
            assert bool(desired & usb.termios.TIOCM_RTS) is rts
        else:
            pytest.fail("separate BIS/BIC control is forbidden")
    monkeypatch.setattr(usb.fcntl, "ioctl", ioctl)
    getattr(obj, update)()
    assert [op for op, _ in calls] == [usb.termios.TIOCMGET, usb.termios.TIOCMSET]


@pytest.mark.parametrize("error", [errno.ENOTTY, errno.EINVAL])
@pytest.mark.parametrize("failed_operation", ["TIOCMGET", "TIOCMSET"])
def test_unsupported_atomic_control_is_not_silently_ignored_by_pyserial_open(monkeypatch, error, failed_operation):
    if usb.termios is None:
        pytest.skip("POSIX test")
    import serial.serialposix as posix
    closed = []
    monkeypatch.setattr(posix.os, "open", lambda *a, **kw: 12345)
    monkeypatch.setattr(posix.os, "close", closed.append)
    monkeypatch.setattr(usb._AtomicSerial, "_reconfigure_port", lambda *a, **kw: None)
    def unsupported(fd, operation, buffer, *args):
        if operation == getattr(usb.termios, failed_operation):
            raise OSError(error, "unsupported atomic control")
        buffer[0] = 0
    monkeypatch.setattr(usb.fcntl, "ioctl", unsupported)
    obj = usb._AtomicSerial(None)
    obj.port = "/dev/cu.test"
    with pytest.raises(serial.SerialException, match="atomic"):
        obj.open()
    assert not obj.is_open and obj.fd is None and closed == [12345]


def test_missing_atomic_platform_support_fails_before_open(monkeypatch):
    monkeypatch.setattr(usb.os, "name", "posix")
    monkeypatch.setattr(usb.list_ports, "comports", lambda: [port()])
    monkeypatch.setattr(usb, "termios", None)
    monkeypatch.setattr(usb.os, "open", lambda *a, **kw: pytest.fail("unsafe open must not happen"))
    with pytest.raises(serial.SerialException, match="atomic"):
        usb.open_provisioning_port("/dev/cu.test")
