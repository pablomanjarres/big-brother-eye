"""Seconds since the last keyboard or mouse input on this computer (None if unknown)."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable


def _windows() -> Callable[[], float | None]:
    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.GetTickCount.restype = ctypes.c_uint

    def read() -> float | None:
        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        elapsed_ms = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF  # both wrap at 2^32
        return elapsed_ms / 1000.0

    return read


def _macos() -> Callable[[], float | None]:
    services = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    seconds_since = services.CGEventSourceSecondsSinceLastEventType
    seconds_since.restype = ctypes.c_double
    seconds_since.argtypes = [ctypes.c_int32, ctypes.c_uint32]
    hid_system_state, any_input = 1, 0xFFFFFFFF

    def read() -> float | None:
        value = float(seconds_since(hid_system_state, any_input))
        return value if value >= 0 else None

    return read


def input_idle_reader() -> Callable[[], float | None]:
    """A function returning idle seconds. On unsupported systems it always returns None."""
    try:
        if sys.platform == "win32":
            return _windows()
        if sys.platform == "darwin":
            return _macos()
    except (OSError, AttributeError):
        pass
    return lambda: None
