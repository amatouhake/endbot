import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

from endbot_cli.terminal import has_terminal

SRC = Path(__file__).resolve().parents[1] / "src"


class TerminalTests(unittest.TestCase):
    def test_a_closed_stdin_or_a_pipe_is_no_terminal(self) -> None:
        closed = io.StringIO()
        closed.close()
        self.assertFalse(has_terminal(closed))
        self.assertFalse(has_terminal(io.StringIO("token\n")))

    def test_stdin_from_the_null_device_is_no_terminal(self) -> None:
        # On Windows NUL claims isatty(); getpass would then wait on the console forever.
        probe = "import sys; from endbot_cli.terminal import has_terminal; print(sys.stdin.isatty(), has_terminal())"
        result = subprocess.run(
            [sys.executable, "-c", probe],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(SRC), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
            check=True,
            timeout=60,
        )
        self.assertTrue(result.stdout.strip().endswith("False"), result.stdout)


if __name__ == "__main__":
    unittest.main()
