"""Tests for the NetherNet LAN discovery port pre-start check."""

from __future__ import annotations

import os
import socket
import unittest
from unittest import mock

from endbot_cli import netcheck
from endbot_cli.netcheck import lan_discovery_port_problem, windows_udp_port_owners


def unreadable(port: int) -> list[int]:
    raise OSError("table unavailable")


def lan_address() -> str | None:
    """A non-loopback local IPv4 address, found without sending traffic."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = probe.getsockname()[0]
    return None if address.startswith("127.") or address == "0.0.0.0" else address


class LanDiscoveryPortTests(unittest.TestCase):
    def test_a_free_port_passes_and_a_held_port_is_explained(self) -> None:
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        problem = lan_discovery_port_problem(port)
        self.assertIsNotNone(problem)
        self.assertIn(f"UDP {port}", problem)
        self.assertIn("world open to LAN", problem)
        holder.close()
        self.assertIsNone(lan_discovery_port_problem(port))

    def test_a_listed_owner_is_reported_even_when_a_trial_bind_succeeds(self) -> None:
        # The Windows Minecraft client holds 7551 without making a second bind fail.
        problem = lan_discovery_port_problem(free_udp_port(), port_owners=lambda port: [5608, 5608])
        self.assertIsNotNone(problem)
        self.assertIn("process 5608)", problem)

    def test_an_unreadable_owner_table_falls_back_to_the_trial_bind(self) -> None:
        self.assertIsNone(lan_discovery_port_problem(free_udp_port(), port_owners=unreadable))

    def test_the_trial_bind_detects_a_holder_on_a_lan_address(self) -> None:
        address = lan_address()
        if address is None:
            self.skipTest("no non-loopback IPv4 address")
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(holder.close)
        holder.bind((address, 0))
        port = holder.getsockname()[1]
        self.assertIsNotNone(lan_discovery_port_problem(port, port_owners=unreadable))

    def test_a_readable_windows_table_needs_no_trial_bind(self) -> None:
        # A wildcard trial bind raises a Windows Firewall prompt per Python install.
        port = free_udp_port()
        with mock.patch.object(netcheck.os, "name", "nt"), mock.patch.object(
            netcheck.socket, "socket", side_effect=AssertionError("trial bind")
        ):
            self.assertIsNone(lan_discovery_port_problem(port, port_owners=lambda port: []))

    @unittest.skipUnless(os.name == "nt", "Windows UDP table")
    def test_the_windows_udp_table_lists_a_shared_socket(self) -> None:
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(holder.close)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        self.assertIn(os.getpid(), windows_udp_port_owners(port))
        holder.close()
        self.assertNotIn(os.getpid(), windows_udp_port_owners(port))


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


if __name__ == "__main__":
    unittest.main()
