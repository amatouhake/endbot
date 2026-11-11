"""Whether there is a person at a console to ask, before prompting for a secret."""

from __future__ import annotations

import sys
from typing import TextIO


def has_terminal(stream: TextIO | None = None) -> bool:
    """False for a service, a scheduled task, a pipe, or stdin redirected from NUL.

    On Windows ``isatty()`` is also true for the NUL device, and ``getpass`` reads
    the console rather than stdin, so a prompt there waits forever. A real
    console is the one whose handle answers ``GetConsoleMode``.
    """

    stream = sys.stdin if stream is None else stream
    try:
        if stream is None or not stream.isatty():
            return False
        if sys.platform != "win32":
            return True
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return False
