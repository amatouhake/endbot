import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import check_translations as ct

ROOT = Path(__file__).resolve().parents[1]


class CheckTranslationsTests(unittest.TestCase):
    """Runs the checker against a throwaway git repository shaped like docs/ and docs/ja/."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.ja = self.root / "docs" / "ja"
        self.ja.mkdir(parents=True)
        subprocess.run(("git", "init", "-q"), cwd=self.root, check=True)
        for name, value in (("ROOT", self.root), ("JA_DIR", self.ja)):
            patcher = mock.patch.object(ct, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write("docs/GUIDE.md", "# Guide\n\nFirst version.\n")
        self.write("docs/OTHER.md", "# Other\n")

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.write_text(text, encoding="utf-8", newline="\n")
        # Adding stores the blob, so a later diff against it can be shown.
        subprocess.run(("git", "add", relative), cwd=self.root, check=True)
        return path

    def blob(self, relative: str) -> str:
        return subprocess.run(
            ("git", "hash-object", relative), cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def translate(self, body: str, name: str = "GUIDE") -> Path:
        header = f"<!-- source: docs/{name}.md {self.blob(f'docs/{name}.md')} -->\n\n"
        return self.write(f"docs/ja/{name}.md", header + body)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.dict(
            "os.environ", {"GITHUB_ACTIONS": ""}
        ):
            code = ct.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_up_to_date_translation_passes(self) -> None:
        self.translate('<a id="guide"></a>\n# ガイド\n\n[その他](../OTHER.md) と [上へ](#guide)\n')
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("1 up to date", out)

    def test_missing_header_fails(self) -> None:
        self.write("docs/ja/GUIDE.md", "# ガイド\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("first line must be", err)

    def test_missing_source_fails(self) -> None:
        self.write("docs/ja/GONE.md", f"<!-- source: docs/GONE.md {'0' * 40} -->\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("source docs/GONE.md does not exist", err)

    def test_broken_file_link_fails(self) -> None:
        self.translate("[その他](OTHER.md)\n")  # OTHER has no Japanese version: must be ../OTHER.md
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("link to missing file OTHER.md", err)

    def test_missing_anchor_in_translation_fails(self) -> None:
        self.translate('<a id="guide"></a>\n# ガイド\n', name="OTHER")
        self.translate("[a](OTHER.md#guide) [b](OTHER.md#nowhere) [c](#here)\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertNotIn("OTHER.md#guide", err)
        self.assertIn("link to missing anchor OTHER.md#nowhere", err)
        self.assertIn("link to missing anchor #here", err)

    def test_hard_wrapped_paragraph_fails_and_unwrap_fixes_it(self) -> None:
        path = self.translate("# ガイド\n\n一行目の文が\n二行目に続きます。\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("paragraph lines are hard-wrapped", err)
        code, _, _ = self.run_main("--unwrap", str(path))
        self.assertEqual(code, 0)
        self.assertIn("一行目の文が二行目に続きます。", path.read_text(encoding="utf-8"))
        code, _, err = self.run_main()
        self.assertEqual(code, 0, err)

    def test_long_paragraph_fails_but_code_and_tables_do_not(self) -> None:
        long = "あ" * (ct.MAX_PARAGRAPH + 1)
        self.translate(f"```text\n{long}\n```\n\n| {long} |\n\n{'い' * ct.MAX_PARAGRAPH}\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.translate(f"# ガイド\n\n- {long}\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn(f"paragraph of {ct.MAX_PARAGRAPH + 3} characters", err)

    def test_anchor_into_english_file_is_not_checked(self) -> None:
        self.translate("[その他](../OTHER.md#any-slug)\n")
        code, _, err = self.run_main()
        self.assertEqual(code, 0, err)

    def test_changed_source_warns_with_diff_and_strict_fails(self) -> None:
        self.translate("# ガイド\n\n最初の版。\n")
        self.write("docs/GUIDE.md", "# Guide\n\nSecond version.\n")
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("docs/GUIDE.md changed since it was translated", out)
        self.assertIn("-First version.", out)
        self.assertIn("+Second version.", out)
        code, _, _ = self.run_main("--strict")
        self.assertEqual(code, 1)

    def test_accept_records_the_current_blob(self) -> None:
        path = self.translate("# ガイド\n")
        self.write("docs/GUIDE.md", "# Guide\n\nSecond version.\n")
        code, _, err = self.run_main("--accept", str(path))
        self.assertEqual(code, 0, err)
        self.assertEqual(
            path.read_text(encoding="utf-8").splitlines()[0],
            f"<!-- source: docs/GUIDE.md {self.blob('docs/GUIDE.md')} -->",
        )
        self.assertIn("# ガイド", path.read_text(encoding="utf-8"))
        code, out, _ = self.run_main("--strict")
        self.assertEqual(code, 0, out)


class UnwrapTests(unittest.TestCase):
    def test_joins_japanese_without_a_space_and_ascii_with_one(self) -> None:
        text = "これは長い\n段落です。Bot は\n`/bot` を使います\nand more.\n"
        self.assertEqual(ct.unwrap(text), "これは長い段落です。Bot は `/bot` を使います and more.\n")

    def test_looks_past_links_emphasis_and_full_width_punctuation(self) -> None:
        self.assertEqual(ct.unwrap("詳しくは\n[コマンド](COMMANDS.md)へ\n"), "詳しくは[コマンド](COMMANDS.md)へ\n")
        self.assertEqual(ct.unwrap("**注意。**\n次へ。\n[UI](UI.md)\n"), "**注意。**次へ。[UI](UI.md)\n")

    def test_keeps_blocks_and_list_items_apart(self) -> None:
        text = (
            '<a id="x"></a>\n# 見出し\n本文\n\n- 項目 1\n  の続き\n- 項目 2\n1. 番号\n'
            "| a | b |\n| --- | --- |\n```text\n/bot Alice\n  stop\n```\n"
        )
        expected = (
            '<a id="x"></a>\n# 見出し\n本文\n\n- 項目 1 の続き\n- 項目 2\n1. 番号\n'
            "| a | b |\n| --- | --- |\n```text\n/bot Alice\n  stop\n```\n"
        )
        self.assertEqual(ct.unwrap(text), expected)

    def test_unwrapped_text_is_stable(self) -> None:
        once = ct.unwrap("一行目\n二行目\n\n- a\n  b\n")
        self.assertEqual(ct.unwrap(once), once)


class RepositoryTranslationsTests(unittest.TestCase):
    def test_repository_translations_have_valid_headers_and_links(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = ct.main([])
        self.assertEqual(code, 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()
