#!/usr/bin/env python3
"""Generate the plugin's block sight/aim/break table from minecraft-data (BOT-028, BOT-030).

The table is committed; regenerate it whenever the runtime's protocol data (`gameVersion` in
`runtime/src/config.js`) or `runtime/package-lock.json`'s minecraft-data moves:

    python scripts/generate_block_properties.py
    python scripts/generate_block_properties.py --blocks path/to/bedrock/<version>/blocks.json

Without --blocks, the minecraft-data package installed under runtime/node_modules (`npm ci --prefix runtime`)
is read. `tests/test_block_properties.py` checks that the committed table names the runtime's protocol data version
and the pinned minecraft-data release. That is the locked BDS version, or an earlier one with the same protocol when
minecraft-data has no data for the locked BDS yet (BDS 1.26.65 uses the 1.26.60 data, protocol 2225).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "plugin/endbot/src/endstone_endbot/block_properties.py"


def runtime_game_version() -> str:
    """The minecraft-data Bedrock version the runtime speaks by default (`gameVersion`)."""
    text = (ROOT / "runtime/src/config.js").read_text(encoding="utf-8")
    match = re.search(r"gameVersion: value\.gameVersion \?\? '([0-9.]+)'", text)
    if match is None:
        raise SystemExit("runtime/src/config.js has no default gameVersion")
    return match.group(1)


def pinned_minecraft_data() -> str:
    packages = json.loads((ROOT / "runtime/package-lock.json").read_text(encoding="utf-8"))["packages"]
    return packages["node_modules/minecraft-data"]["version"]


def render(blocks: list[dict], bedrock: str, release: str) -> str:
    def names(predicate):
        selected = sorted("minecraft:" + block["name"] for block in blocks if predicate(block))
        return "frozenset({\n" + "".join(f"    {name!r},\n" for name in selected) + "})"

    return f'''"""Block properties from minecraft-data {release}, Bedrock {bedrock}. Generated; do not edit.

Regenerate with `python scripts/generate_block_properties.py` (see that script).
"""

MINECRAFT_DATA_VERSION = {release!r}
BEDROCK_DATA_VERSION = {bedrock!r}

# Light passes (`transparent`): air, liquids, plants, leaves, glass, ice and partial shapes such as slabs, stairs,
# fences, walls, panes and bars. A player sees the blocks around them, so `blocks` lists what touches them.
SEE_THROUGH = {names(lambda block: block.get("transparent"))}

# No collision box (`boundingBox: empty`): air, liquids, plants, torches, rails, buttons, signs, snow layers.
# An explicitly named target is aimed at through them.
NO_COLLISION = {names(lambda block: block.get("boundingBox") == "empty")}

# Not diggable in Survival (`diggable: false`): bedrock, barriers, portals, command blocks, air and liquids.
UNBREAKABLE = {names(lambda block: not block.get("diggable"))}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blocks", type=Path, help="a minecraft-data bedrock/<version>/blocks.json")
    arguments = parser.parse_args()
    bedrock, release = runtime_game_version(), pinned_minecraft_data()
    path = arguments.blocks or (ROOT / "runtime/node_modules/minecraft-data/minecraft-data/data/bedrock"
                                / bedrock / "blocks.json")
    blocks = json.loads(path.read_text(encoding="utf-8"))
    OUTPUT.write_text(render(blocks, bedrock, release), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(blocks)} blocks, Bedrock {bedrock}, minecraft-data {release})")


if __name__ == "__main__":
    main()
