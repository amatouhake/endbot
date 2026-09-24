"""Contract test for the pinned Endstone private API used by `endbot setup` (section 6).

``endbot_cli.bds`` drives ``endstone.cli.base.Bootstrap._install()`` (whose
``_update`` / ``_download`` steps it triggers) through exactly pinned private
methods. This test inspects the real Endstone when it is installed (skipped
otherwise) so an Endstone bump that changes the bootstrap fails loudly here
instead of at an operator's first setup.
"""

from __future__ import annotations

import inspect
import platform
import unittest

try:
    import endstone.cli.base as endstone_base
except Exception:  # noqa: BLE001 - pragma: no cover - endstone is optional for the CLI package
    endstone_base = None  # type: ignore[assignment]


@unittest.skipIf(endstone_base is None, "endstone is not installed in this environment")
class BootstrapContractTests(unittest.TestCase):
    def test_bootstrap_exposes_the_private_install_update_download_steps(self) -> None:
        for name in ("_install", "_update", "_download"):
            with self.subTest(name=name):
                self.assertTrue(
                    callable(getattr(endstone_base.Bootstrap, name, None)),
                    f"endstone.cli.base.Bootstrap.{name} is gone; endbot_cli.bds must be updated with the pin",
                )

    def test_platform_bootstrap_accepts_the_documented_constructor_arguments(self) -> None:
        system = platform.system()
        if system == "Windows":
            from endstone.cli.windows import WindowsBootstrap as bootstrap_class
        elif system == "Linux":
            from endstone.cli.linux import LinuxBootstrap as bootstrap_class
        else:  # pragma: no cover - endstone supports Windows and Linux only
            self.skipTest(f"{system} has no platform bootstrap to inspect")
        parameters = inspect.signature(bootstrap_class.__init__).parameters
        for expected in ("server_folder", "no_confirm", "remote", "interactive"):
            with self.subTest(parameter=expected):
                self.assertIn(expected, parameters)

    def test_bootstrap_install_tolerates_no_confirm(self) -> None:
        parameters = inspect.signature(endstone_base.Bootstrap.__init__).parameters
        self.assertIn("no_confirm", parameters)


if __name__ == "__main__":
    unittest.main()
