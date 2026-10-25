#!/usr/bin/env python3
"""Validate and package the optional Endbot UI resource pack (``ui-pack/``, docs/UI_PACK.md).

The pack is client-side only: players add it as a global resource pack, the
server's pack stack stays empty. This script checks what can be checked
without a Minecraft client:

- every pack file is strict JSON and the manifest is well formed;
- every element the pack inherits from or points at exists, either in the
  pack itself or in the list of vanilla elements it was written against;
- with ``--vanilla <resource_pack dir>`` (Mojang's bedrock-samples, fetched by
  the operator), that those vanilla elements really exist in that version.

Whether the client renders and submits the forms as intended is a manual gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "ui-pack"
PACK_FILES = ("manifest.json", "ui/server_form.json")
# The vanilla version the overrides were written against (bedrock-samples tag/date).
VANILLA_VERSION = "1.26.50.4"
# Vanilla elements the pack inherits from or names. A new client version may rename one;
# `--vanilla` confirms them against that version's bedrock-samples resource pack.
VANILLA_ELEMENTS = {
    "common.scrolling_panel": "ui/ui_common.json",
    "common.scrollable_multiline_text_edit_box": "ui/ui_common.json",
    "common_buttons.light_text_button": "ui/ui_template_buttons.json",
    "settings_common.option_group_section_divider": "ui/settings_sections/settings_common.json",
    "server_form.long_form": "ui/server_form.json",
    "server_form.long_form_panel": "ui/server_form.json",
    "server_form.long_form_scrolling_content": "ui/server_form.json",
    "server_form.long_form_dynamic_buttons_panel": "ui/server_form.json",
    "server_form.dynamic_button": "ui/server_form.json",
    "server_form.dynamic_label": "ui/server_form.json",
    "server_form.dynamic_header": "ui/server_form.json",
    "server_form.custom_form": "ui/server_form.json",
    "server_form.custom_form_panel": "ui/server_form.json",
    "server_form.custom_form_scrolling_content": "ui/server_form.json",
    "server_form.generated_contents": "ui/server_form.json",
    "server_form.custom_label": "ui/server_form.json",
    "server_form.custom_toggle": "ui/server_form.json",
    "server_form.custom_slider": "ui/server_form.json",
    "server_form.custom_step_slider": "ui/server_form.json",
    "server_form.custom_dropdown": "ui/server_form.json",
    "server_form.custom_input": "ui/server_form.json",
    "server_form.custom_header": "ui/server_form.json",
    "server_form.custom_multiselect": "ui/server_form.json",
}
# Vanilla elements the pack redefines; everything else it adds is named endbot_*.
REDEFINED = {"main_screen_content"}
# Variables whose value names another element.
ELEMENT_VARIABLES = {"$child_control", "$scrolling_content", "$control_name"}
REFERENCE = re.compile(r"@?([a-z_]+)\.([A-Za-z0-9_]+)\Z")
# Formatting codes the plugin puts before multi-line input labels (forms.MULTILINE_MARKER).
MULTILINE_MARKER = "§e§b§o§t§r"


class PackError(ValueError):
    pass


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackError(f"{path.relative_to(path.parents[1]) if len(path.parents) > 1 else path}: {error}") from error


def check_manifest(manifest: dict) -> None:
    header, modules = manifest.get("header"), manifest.get("modules")
    if manifest.get("format_version") != 2 or not isinstance(header, dict) or not isinstance(modules, list):
        raise PackError("manifest.json: expected format_version 2 with a header and modules")
    ids = [header.get("uuid"), *(module.get("uuid") for module in modules)]
    for value in ids:
        try:
            uuid.UUID(str(value))
        except ValueError as error:
            raise PackError(f"manifest.json: invalid uuid {value!r}") from error
    if len(set(ids)) != len(ids):
        raise PackError("manifest.json: header and module uuids must differ")
    for part in (header, *modules):
        version = part.get("version")
        if not (isinstance(version, list) and len(version) == 3 and all(type(n) is int and n >= 0 for n in version)):
            raise PackError(f"manifest.json: version must be three non-negative integers, not {version!r}")
    if [module.get("type") for module in modules] != ["resources"]:
        raise PackError("manifest.json: the pack must be one resources module (no behavior or script modules)")


def references(ui: dict) -> list[tuple[str, str]]:
    """(where, namespace.element) for every element the file inherits from or points at."""
    found = []

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if "@" in key and not key.startswith("$"):
                    found.append((where + "/" + key, key.split("@", 1)[1]))
                if key in ELEMENT_VARIABLES and isinstance(value, str):
                    found.append((where + "/" + key, value))
                if key == "control_ids" and isinstance(value, dict):
                    found.extend((f"{where}/control_ids/{name}", target) for name, target in value.items())
                walk(value, where + "/" + key.split("@", 1)[0])
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{where}[{index}]")

    walk(ui, "")
    return found


def check_server_form(ui: dict) -> None:
    if ui.get("namespace") != "server_form":
        raise PackError("ui/server_form.json: namespace must be server_form")
    defined = {key.split("@", 1)[0] for key in ui if key != "namespace"}
    for name in defined - REDEFINED:
        if not name.startswith("endbot_"):
            raise PackError(f"ui/server_form.json: {name} redefines a vanilla element; add endbot_* elements instead")
    for where, target in references(ui):
        match = REFERENCE.match(target)
        if not match:
            raise PackError(f"ui/server_form.json{where}: {target!r} is not a namespace.element reference")
        namespace, element = match.groups()
        if namespace == "server_form" and element in defined:
            continue
        if f"{namespace}.{element}" not in VANILLA_ELEMENTS:
            raise PackError(f"ui/server_form.json{where}: unknown element {namespace}.{element}")
    text = json.dumps(ui, ensure_ascii=False)
    if text.count(f"'{MULTILINE_MARKER}'") != 2:
        raise PackError("ui/server_form.json: the multi-line marker must select exactly the two input variants")


def strip_comments(text: str) -> str:
    """Vanilla UI files allow // and /* */ comments; drop them outside strings."""
    out, index, in_string = [], 0, False
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(text[index + 1 : index + 2])
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif text.startswith("//", index):
            index = text.find("\n", index)
            if index < 0:
                break
            continue
        elif text.startswith("/*", index):
            index = text.find("*/", index) + 2
            if index < 2:
                break
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def check_vanilla(vanilla: Path) -> int:
    """Confirm VANILLA_ELEMENTS against a bedrock-samples ``resource_pack`` directory."""
    cache: dict[str, set[str]] = {}
    for qualified, relative in VANILLA_ELEMENTS.items():
        namespace, element = qualified.split(".", 1)
        if relative not in cache:
            path = vanilla / relative
            try:
                data = json.loads(strip_comments(path.read_text(encoding="utf-8-sig")))
            except (OSError, ValueError) as error:
                raise PackError(f"vanilla {relative}: {error}") from error
            if data.get("namespace") != namespace:
                raise PackError(f"vanilla {relative}: namespace is {data.get('namespace')!r}, not {namespace!r}")
            cache[relative] = {key.split("@", 1)[0] for key in data if key != "namespace"}
        if element not in cache[relative]:
            raise PackError(f"vanilla {relative}: element {qualified} is missing")
    return len(VANILLA_ELEMENTS)


def check_pack(pack: Path = PACK_DIR) -> dict[str, bytes]:
    files = sorted(str(path.relative_to(pack).as_posix()) for path in pack.rglob("*") if path.is_file())
    if files != sorted(PACK_FILES):
        raise PackError(f"ui-pack holds {files}; expected exactly {sorted(PACK_FILES)}")
    check_manifest(load(pack / "manifest.json"))
    check_server_form(load(pack / "ui" / "server_form.json"))
    return {name: (pack / name).read_bytes() for name in PACK_FILES}


def build(output: Path, pack: Path = PACK_DIR) -> Path:
    """Write a reproducible .mcpack (a zip with manifest.json at its root)."""
    contents = check_pack(pack)
    version = ".".join(str(n) for n in load(pack / "manifest.json")["header"]["version"])
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"endbot-ui-{version}.mcpack"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, contents[name])
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "ui-pack", help="output directory")
    parser.add_argument("--check", action="store_true", help="validate only; write nothing")
    parser.add_argument("--vanilla", type=Path,
                        help=f"bedrock-samples resource_pack directory to confirm vanilla elements "
                             f"(written against {VANILLA_VERSION})")
    args = parser.parse_args(argv)
    try:
        if args.vanilla:
            print(f"vanilla elements confirmed: {check_vanilla(args.vanilla)}")
        if args.check:
            check_pack()
            print("ui-pack is valid")
        else:
            print(build(args.out))
    except PackError as error:
        print(f"ui-pack check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
