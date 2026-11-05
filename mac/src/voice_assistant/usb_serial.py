"""USB provisioning ports that avoid resetting Espressif native USB devices.

On POSIX, pySerial applies DTR and RTS in separate ioctl calls during open.
The intermediate combination resets the ESP32-C6 USB Serial/JTAG peripheral.
Update both modem bits together, following Espressif's atomic control primitive:
https://github.com/espressif/esp-pylib/blob/master/esp_pylib/serial_reset.py
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import sys
from typing import Any

import serial
from serial.tools import list_ports

try:
    import fcntl
    import termios
except ImportError:  # Keep ordinary non-POSIX serial ports compatible.
    fcntl = None
    termios = None

_POSIX = os.name == "posix"
_ESPRESSIF_NATIVE_USB = (0x303A, 0x1001)


def _port_path(name: str) -> str:
    path = os.path.realpath(name)
    # macOS exposes call-in and call-out names for the same physical port;
    # enumeration normally returns only the call-out name.
    if sys.platform == "darwin" and path.startswith("/dev/tty."):
        path = "/dev/cu." + path.removeprefix("/dev/tty.")
    return path


def _port_info(port_name: str) -> Any | None:
    requested = _port_path(port_name)
    return next((info for info in list_ports.comports()
                 if _port_path(info.device) == requested), None)


def usb_port_identity(port_name: str) -> str | None:
    """Hash stable USB hardware metadata, without opening the port.

    A path, port index or human description is not a physical-device identity.
    Devices without a serial number cannot safely skip later provisioning.
    """
    try:
        info = _port_info(port_name)
    except (OSError, serial.SerialException):
        return None
    if info is None:
        return None
    vid, pid = getattr(info, "vid", None), getattr(info, "pid", None)
    number = getattr(info, "serial_number", None)
    if not isinstance(vid, int) or not isinstance(pid, int) or not isinstance(number, str) or not number.strip():
        return None
    encoded = json.dumps({"vid": vid, "pid": pid, "serial_number": number},
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_atomic_support() -> None:
    if (not _POSIX or fcntl is None or termios is None
            or not hasattr(fcntl, "ioctl")
            or any(not hasattr(termios, name)
                   for name in ("TIOCMGET", "TIOCMSET", "TIOCM_DTR", "TIOCM_RTS"))):
        raise serial.SerialException("Safe native USB open requires atomic DTR/RTS control; this platform does not support it")


class _AtomicSerial(serial.Serial):
    def open(self) -> None:
        # Refuse an unsafe native-USB open before touching the device.
        _require_atomic_support()
        super().open()

    def _atomic_lines(self) -> None:
        _require_atomic_support()
        try:
            bits = array.array("i", [0])
            fcntl.ioctl(self.fd, termios.TIOCMGET, bits, True)
            desired = bits[0] & ~(termios.TIOCM_DTR | termios.TIOCM_RTS)
            if self._dtr_state:
                desired |= termios.TIOCM_DTR
            if self._rts_state:
                desired |= termios.TIOCM_RTS
            fcntl.ioctl(self.fd, termios.TIOCMSET, array.array("i", [desired]))
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            # pySerial deliberately ignores OSError(EINVAL/ENOTTY) during open.
            # A fresh SerialException has no errno, so it propagates and base
            # open closes its fd. Never fall back to sequential modem writes.
            raise serial.SerialException("Safe native USB atomic DTR/RTS control failed; provisioning port was not opened safely") from exc

    def _update_dtr_state(self) -> None:
        self._atomic_lines()

    def _update_rts_state(self) -> None:
        self._atomic_lines()


def open_provisioning_port(port_name: str) -> serial.Serial:
    """Open one identified provisioning port; never probe by opening devices."""
    try:
        info = _port_info(port_name)
    except (OSError, serial.SerialException) as exc:
        raise serial.SerialException("Cannot identify the provisioning USB port safely") from exc
    native = info is not None and (getattr(info, "vid", None), getattr(info, "pid", None)) == _ESPRESSIF_NATIVE_USB
    port_type = _AtomicSerial if _POSIX and native else serial.Serial
    port = port_type(None, 115200, timeout=.3, write_timeout=5, dsrdtr=False, rtscts=False)
    port.dtr = False
    port.rts = False
    port.port = port_name
    port.open()
    return port
