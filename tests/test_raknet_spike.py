"""Offline parser/lease checks for the opt-in endbotd failure scenarios."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import raknet_spike


class RaknetSpikeEndbotdFailureTests(unittest.TestCase):
    def test_parser_accepts_endbotd_failures_with_the_other_flags(self):
        args = raknet_spike.build_parser().parse_args([
            "--output", "build/spike",
            "--mining", "--scripting", "--endbotd-failures",
            "--transport", "nethernet",
        ])
        self.assertTrue(args.endbotd_failures)
        self.assertTrue(args.mining)
        self.assertTrue(args.scripting)
        self.assertEqual(args.transport, "nethernet")

    def test_parser_defaults_to_no_endbotd_failures(self):
        args = raknet_spike.build_parser().parse_args(["--output", "build/spike"])
        self.assertFalse(args.endbotd_failures)

    def test_cli_forwards_endbotd_failures_and_leaves_the_other_flags_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fresh-output"
            argv = [
                "raknet_spike.py", "--output", str(output), "--node", "node",
                "--endbotd-failures", "--scripting", "--mining",
            ]

            def exercise(output, node, report, **kwargs):
                report["ok"] = True

            with patch.object(sys, "argv", argv), \
                 patch.object(raknet_spike.subprocess, "run",
                              return_value=SimpleNamespace(returncode=0)), \
                 patch.object(raknet_spike, "exercise", side_effect=exercise) as run, \
                 patch("builtins.print"):
                self.assertEqual(raknet_spike.main(), 0)
                self.assertTrue(run.call_args.kwargs["endbotd_failures"])
                self.assertTrue(run.call_args.kwargs["scripting"])
                self.assertTrue(run.call_args.kwargs["mining"])

    def test_mining_lease_is_read_from_the_runtime_source(self):
        source = (ROOT / "runtime/src/protocol-session.js").read_text(encoding="utf-8")
        self.assertEqual(raknet_spike.mining_lease_seconds(source), 10.0)

    def test_mining_lease_rejects_a_source_without_the_expected_lease(self):
        with self.assertRaises(ValueError):
            raknet_spike.mining_lease_seconds("setInterval(() => {}, 50)")


if __name__ == "__main__":
    unittest.main()
