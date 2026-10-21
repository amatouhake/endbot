"""Pre-start check that BDS can own the NetherNet LAN discovery port.

NetherNet Bots reach BDS through LAN discovery on UDP 7551. When another
program already holds that port (typically a Minecraft client with a world open
to LAN on the same machine), BDS cannot answer discovery, so Bots can never
join. ``endbot start`` refuses up front with this explanation instead of
starting a server the runtime cannot reach.

On Windows a trial bind is not enough: the Minecraft client's 7551 socket does
not make a second bind fail (even with ``SO_EXCLUSIVEADDRUSE``), yet BDS then
never receives discovery. The check therefore also reads the system UDP table.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable

NETHERNET_DISCOVERY_PORT = 7551


def windows_udp_port_owners(port: int) -> list[int]:
    """Return the PIDs of IPv4 UDP sockets bound to ``port`` (Windows only)."""

    import ctypes
    from ctypes import wintypes

    udp_table_owner_pid = 1
    no_error, insufficient_buffer = 0, 122

    class Row(ctypes.Structure):
        _fields_ = [
            ("local_address", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("owning_pid", wintypes.DWORD),
        ]

    function = ctypes.windll.iphlpapi.GetExtendedUdpTable
    size = wintypes.DWORD(0)
    result = insufficient_buffer
    buffer = None
    while result == insufficient_buffer:
        buffer = ctypes.create_string_buffer(size.value or 4)
        result = function(buffer, ctypes.byref(size), False, socket.AF_INET, udp_table_owner_pid, 0)
    if result != no_error:
        raise OSError(result, "GetExtendedUdpTable failed")
    count = wintypes.DWORD.from_buffer(buffer).value
    rows = (Row * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
    # The port is stored in network byte order in the low 16 bits.
    return [row.owning_pid for row in rows if socket.ntohs(row.local_port & 0xFFFF) == port]


def _problem(port: int, detail: str) -> str:
    return (
        f"UDP {port} (NetherNet LAN discovery) is already in use ({detail}); another program, "
        "usually Minecraft with a world open to LAN on this machine, is holding it, so BDS could not answer "
        "discovery and Bots could never join. Close that world (or stop that program) and run `endbot start` again"
    )


def lan_discovery_port_problem(
    port: int = NETHERNET_DISCOVERY_PORT,
    port_owners: Callable[[int], list[int]] | None = None,
) -> str | None:
    """Return a problem description when ``port`` cannot be owned for UDP, else None."""

    if port_owners is None and os.name == "nt":
        port_owners = windows_udp_port_owners
    if port_owners is not None:
        try:
            owners = port_owners(port)
        except OSError:
            owners = []  # Fall back to the trial bind below.
        if owners:
            return _problem(port, "process " + ", ".join(str(pid) for pid in sorted(set(owners))))
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Loopback still collides with a wildcard holder, and unlike 0.0.0.0 it
        # does not raise a Windows Firewall prompt for each Python install.
        probe.bind(("127.0.0.1", port))
    except OSError as error:
        return _problem(port, f"errno {error.errno}")
    finally:
        probe.close()
    return None
