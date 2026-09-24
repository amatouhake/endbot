"""Pre-start check that BDS can own the NetherNet LAN discovery port.

Bots reach BDS through NetherNet LAN discovery on UDP 7551. When another
program already holds that port (typically a Minecraft client with a world open
to LAN on the same machine), BDS cannot answer discovery, so Bots can never
join. ``endbot start`` refuses up front with this explanation instead of
starting a server the runtime cannot reach.
"""

from __future__ import annotations

import socket

NETHERNET_DISCOVERY_PORT = 7551


def lan_discovery_port_problem(port: int = NETHERNET_DISCOVERY_PORT) -> str | None:
    """Return a problem description when ``port`` cannot be bound for UDP, else None."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("0.0.0.0", port))
    except OSError as error:
        return (
            f"UDP {port} (NetherNet LAN discovery) is already in use (errno {error.errno}); another program, "
            "usually Minecraft with a world open to LAN on this machine, is holding it, so BDS could not answer "
            "discovery and Bots could never join. Close that world (or stop that program) and run `endbot start` again"
        )
    finally:
        probe.close()
    return None
