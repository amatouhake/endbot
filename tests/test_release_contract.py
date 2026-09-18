from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWheelContractTests(unittest.TestCase):
    def test_release_candidate_repairs_and_inspects_endstone_wheel(self) -> None:
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/build_endstone_wheel.py", workflow)
        self.assertIn("python scripts/inspect_linux_wheel.py", workflow)
        self.assertIn("--output-dir dist", workflow)
        self.assertNotIn("python -m build --wheel --outdir dist build/endstone-patched", workflow)

        builder = (ROOT / "scripts/build_endstone_wheel.py").read_text(encoding="utf-8")
        self.assertRegex(builder, r'"-m",\s+"cibuildwheel",')
        self.assertIn('"git", "clone", "--quiet", "--local", "--no-hardlinks"', builder)
        self.assertIn("cwd=isolated_source", builder)
        self.assertNotIn('"-m", "build"', builder)

    def test_full_validation_uses_repaired_wheel_for_paired_install(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        build = workflow.index("python scripts/build_endstone_wheel.py")
        inspect = workflow.index("python scripts/inspect_linux_wheel.py", build)
        install = workflow.index("python scripts/test_package_install.py", inspect)
        self.assertLess(build, inspect)
        self.assertLess(inspect, install)


if __name__ == "__main__":
    unittest.main()
