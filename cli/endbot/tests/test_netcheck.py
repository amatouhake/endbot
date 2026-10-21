"""Tests for the NetherNet LAN discovery port pre-start check."""

from __future__ import annotations

import os
import socket
import unittest

from endbot_cli.netcheck import lan_discovery_port_problem, windows_udp_port_owners


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
        def unreadable(port: int) -> list[int]:
            raise OSError("table unavailable")

        self.assertIsNone(lan_discovery_port_problem(free_udp_port(), port_owners=unreadable))

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
