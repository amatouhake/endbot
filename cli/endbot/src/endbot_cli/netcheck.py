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
import time
from collections.abc import Callable

NETHERNET_DISCOVERY_PORT = 7551


def windows_udp_port_owners(port: int) -> list[int]:
    """Return the PIDs of IPv4 and IPv6 UDP sockets bound to ``port`` (Windows only).

    A dual-stack ``[::]`` socket (IPV6_V6ONLY off) also takes IPv4 discovery,
    and it appears only in the IPv6 table, so both tables are read.
    """

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

    class Row6(ctypes.Structure):
        _fields_ = [
            ("local_address", ctypes.c_ubyte * 16),
            ("local_scope_id", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("owning_pid", wintypes.DWORD),
        ]

    function = ctypes.windll.iphlpapi.GetExtendedUdpTable
    owners = []
    for family, row_type in ((socket.AF_INET, Row), (socket.AF_INET6, Row6)):
        size = wintypes.DWORD(0)
        result = insufficient_buffer
        buffer = None
        while result == insufficient_buffer:
            buffer = ctypes.create_string_buffer(size.value or 4)
            result = function(buffer, ctypes.byref(size), False, family, udp_table_owner_pid, 0)
        if result != no_error:
            raise OSError(result, "GetExtendedUdpTable failed")
        count = wintypes.DWORD.from_buffer(buffer).value
        rows = (row_type * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
        # The port is stored in network byte order in the low 16 bits.
        owners += [row.owning_pid for row in rows if socket.ntohs(row.local_port & 0xFFFF) == port]
    return owners


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
            owners = None  # Fall back to the trial bind below.
        if owners:
            return _problem(port, "process " + ", ".join(str(pid) for pid in sorted(set(owners))))
        if owners is not None and os.name == "nt":
            # The Windows tables list every IPv4 and IPv6 UDP socket, LAN-address
            # and dual-stack ones included. Skipping the wildcard trial bind avoids
            # a Windows Firewall prompt for each Python install that runs this check.
            return None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Only a wildcard bind collides with a holder on any local address.
        probe.bind(("0.0.0.0", port))
    except OSError as error:
        return _problem(port, f"errno {error.errno}")
    finally:
        probe.close()
    return None


# Linux keeps a closed TCP connection in TIME-WAIT for 60 s.
SERVER_PORT_WAIT_SECONDS = 75.0


def tcp_port_bindable(port: int) -> bool:
    """Whether a wildcard TCP listener could bind ``port`` without ``SO_REUSEADDR``, as BDS does."""

    for family, address in ((socket.AF_INET6, "::"), (socket.AF_INET, "0.0.0.0")):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        try:
            if family == socket.AF_INET6:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            probe.bind((address, port))
        except OSError:
            return False
        finally:
            probe.close()
        return True
    return True


def wait_for_server_port(
    port: int,
    *,
    timeout: float = SERVER_PORT_WAIT_SECONDS,
    bindable: Callable[[int], bool] = tcp_port_bindable,
    notify: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """Wait until BDS can listen on TCP ``port``; return a problem description if it never can.

    BDS answers NetherNet HTTP signalling (``/v1/join``) with ``Connection: close`` and closes first, so every
    join by address, a Bot's direct signalling included, leaves a TIME-WAIT entry on ``server-port``. On Linux
    the patched BDS then cannot bind the port again until it expires, and a restart right after a stop failed
    with "Port [...] may be in use". Windows binds through TIME-WAIT, so callers skip this there.
    """

    deadline = clock() + timeout
    waited = False
    while not bindable(port):
        if clock() >= deadline:
            return (
                f"TCP {port} (server-port) stayed in use for {int(timeout)} s; another program is listening on it. "
                "Stop that program or give this instance another server-port, then run `endbot start` again"
            )
        if not waited:
            notify(f"start: waiting for TCP {port} (server-port) to be released by the previous BDS (TIME-WAIT, "
                   "up to 60 s on Linux)")
            waited = True
        sleep(1.0)
    return None
