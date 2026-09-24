"""Tests for the NetherNet LAN discovery port pre-start check."""

from __future__ import annotations

import socket
import unittest

from endbot_cli.netcheck import lan_discovery_port_problem


class LanDiscoveryPortTests(unittest.TestCase):
    def test_a_free_port_passes_and_a_held_port_is_explained(self) -> None:
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(holder.close)
        holder.bind(("0.0.0.0", 0))
        port = holder.getsockname()[1]
        problem = lan_discovery_port_problem(port)
        self.assertIsNotNone(problem)
        self.assertIn(f"UDP {port}", problem)
        self.assertIn("world open to LAN", problem)
        holder.close()
        self.assertIsNone(lan_discovery_port_problem(port))


if __name__ == "__main__":
    unittest.main()
