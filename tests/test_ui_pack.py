import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_ui_pack import (
    MULTILINE_MARKER,
    PACK_DIR,
    PackError,
    build,
    check_icon,
    check_pack,
    check_vanilla,
    strip_comments,
)

ROOT = Path(__file__).resolve().parents[1]


class UiPackTests(unittest.TestCase):
    def copy_pack(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        pack = Path(directory.name) / "ui-pack"
        shutil.copytree(PACK_DIR, pack)
        return pack

    def edit(self, path: Path, change) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_tracked_pack_is_valid(self) -> None:
        self.assertEqual(sorted(check_pack()),
                         ["LICENSE", "NOTICE", "manifest.json", "pack_icon.png", "ui/server_form.json"])

    def test_marker_matches_the_plugin(self) -> None:
        forms = (ROOT / "core/src/endbot_core/forms.py").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^MULTILINE_MARKER = "([^"]+)"$', forms, re.MULTILINE).group(1), MULTILINE_MARKER)

    def test_multiline_control_binds_the_form_collection(self) -> None:
        forms = json.loads((PACK_DIR / "ui/server_form.json").read_text(encoding="utf-8"))
        control = forms["endbot_multiline_control@common.scrollable_multiline_text_edit_box"]
        self.assertEqual(control["$text_box_enabled_binding_name"], "#custom_input_enabled")
        self.assertEqual(control["$text_edit_box_enabled_binding_type"], "collection")
        self.assertNotIn("$option_binding_name", json.dumps(control))
        self.assertIn({
            "binding_type": "collection",
            "binding_name": "#custom_text",
            "binding_name_override": "#focus_identifier",
            "binding_collection_name": "custom_form",
            "binding_condition": "once",
        }, control["bindings"])

    def test_build_is_reproducible_and_holds_only_the_pack(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        first = build(Path(directory.name) / "a")
        second = build(Path(directory.name) / "b")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.namelist(),
                             ["LICENSE", "NOTICE", "manifest.json", "pack_icon.png", "ui/server_form.json"])

    def test_rejects_broken_packs(self) -> None:
        cases = {
            "redefines a vanilla element": ("ui/server_form.json", lambda d: d.update({"custom_input": {}})),
            "unknown element": (
                "ui/server_form.json",
                lambda d: d["endbot_custom_form_panel@server_form.custom_form_panel"].update(
                    {"$scrolling_content": "server_form.endbot_missing"}),
            ),
            "one resources module": (
                "manifest.json", lambda d: d["modules"].append({"type": "script", "uuid": "0" * 32, "version": [1, 0, 0]})),
            "uuids must differ": (
                "manifest.json", lambda d: d["modules"][0].update({"uuid": d["header"]["uuid"]})),
            "marker": (
                "ui/server_form.json",
                lambda d: d["endbot_input"]["controls"][1]["multi_line"]["bindings"][1].update(
                    {"source_property_name": "(#custom_text = '')"}),
            ),
        }
        for message, (name, change) in cases.items():
            with self.subTest(message):
                pack = self.copy_pack()
                self.edit(pack / name, change)
                with self.assertRaisesRegex(PackError, message):
                    check_pack(pack)
        pack = self.copy_pack()
        (pack / "ui" / "extra.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(PackError, "expected exactly"):
            check_pack(pack)

    def test_the_icon_must_be_a_square_png(self) -> None:
        def png(width: int, height: int) -> bytes:
            return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
                    + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)

        pack = self.copy_pack()
        (pack / "pack_icon.png").write_bytes(png(64, 64))
        with self.assertRaisesRegex(PackError, "64x64, expected 128x128"):
            check_pack(pack)
        (pack / "pack_icon.png").write_bytes(b"not a png")
        with self.assertRaisesRegex(PackError, "not a PNG"):
            check_pack(pack)
        (pack / "pack_icon.png").unlink()
        with self.assertRaisesRegex(PackError, "^pack_icon.png: "):
            check_icon(pack)

    def test_vanilla_check_reads_commented_files_and_reports_missing_elements(self) -> None:
        self.assertEqual(json.loads(strip_comments('/* c */ {"a": "x // y", // z\n "b": 1}')), {"a": "x // y", "b": 1})
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        vanilla = Path(directory.name)
        (vanilla / "ui").mkdir()
        (vanilla / "ui" / "ui_common.json").write_text('// header\n{"namespace": "common"}', encoding="utf-8")
        with self.assertRaisesRegex(PackError, "common.scrolling_panel is missing"):
            check_vanilla(vanilla)


if __name__ == "__main__":
    unittest.main()
