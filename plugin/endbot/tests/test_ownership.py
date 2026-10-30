"""The Bot and Group ownership store (BOT-024 4a); synthetic identities only."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endbot_core.ownership import (
    KINDS,
    VISIBILITIES,
    Ownership,
    OwnershipStore,
    load_ownership,
    owner_key,
)
from endbot_core.principal import Principal

XUID = "2535400000000001"
OTHER_XUID = "2535400000000002"
PLAYER = "0000000a-0000-4000-8000-00000000000b"
OTHER_PLAYER = "0000000c-0000-4000-8000-00000000000d"
BOT = "00000000-0000-4000-8000-0000000000aa"
GROUP = "00000000-0000-4000-8000-0000000000bb"


def principal(name="Alice", *, xuid=XUID, player_id=PLAYER):
    return Principal("player", name=name, xuid=xuid, player_id=player_id)


def entry(owner=f"xuid:{XUID}", owner_name="Alice", visibility="shared"):
    return {"owner": owner, "ownerName": owner_name, "visibility": visibility}


class OwnerKeyTests(unittest.TestCase):
    def test_console_and_no_principal_have_no_owner_key(self):
        self.assertIsNone(owner_key(None))
        self.assertIsNone(owner_key(Principal("console")))
        self.assertIsNone(owner_key(Principal("run", player_id=None, xuid=None)))

    def test_xuid_wins_over_the_player_uuid(self):
        self.assertEqual(owner_key(principal()), f"xuid:{XUID}")
        self.assertEqual(owner_key(Principal("player", xuid=XUID)), f"xuid:{XUID}")

    def test_uuid_only_principal_uses_the_player_key(self):
        self.assertEqual(owner_key(principal(xuid=None)), f"player:{PLAYER}")

    def test_script_service_owner_key_is_unchanged_for_players(self):
        from endbot_core.scripting import ScriptService

        self.assertEqual(ScriptService.owner_key(Principal("console")), "console")
        self.assertEqual(ScriptService.owner_key(Principal("player", player_id=PLAYER, xuid=XUID)), f"xuid:{XUID}")
        self.assertEqual(ScriptService.owner_key(Principal("player", player_id=PLAYER)), f"player:{PLAYER}")


class OwnershipStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "state" / "ownership.json"

    def make_symlink(self) -> Path:
        self.path.parent.mkdir(parents=True)
        target = self.path.parent / "target.json"
        target.write_text(json.dumps({"version": 1, "bots": {}, "groups": {}}), encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks is not permitted on this platform")
        return target

    def test_console_records_nothing(self):
        store = OwnershipStore(self.path)
        for kind, identity in (("bots", BOT), ("groups", GROUP)):
            store.record(kind, identity, Principal("console"))
            store.record(kind, identity, None)
            self.assertIsNone(store.get(kind, identity))
        self.assertFalse(self.path.exists())

    def test_record_stores_the_owner_and_the_kind_default_without_overwriting(self):
        store = OwnershipStore(self.path, bots_default="private", groups_default="private")
        store.record("bots", BOT, principal())
        store.record("bots", BOT, principal("Bob", xuid=OTHER_XUID, player_id=OTHER_PLAYER))
        store.record("groups", GROUP, principal())
        self.assertEqual(store.get("bots", BOT), Ownership(f"xuid:{XUID}", "Alice", "private"))
        self.assertEqual(store.get("groups", GROUP), Ownership(f"xuid:{XUID}", "Alice", "private"))
        store.record("bots", BOT.upper(), principal("Bob", xuid=OTHER_XUID))
        self.assertEqual(store.get("bots", BOT), Ownership(f"xuid:{XUID}", "Alice", "private"))

    def test_record_keeps_defaults_per_kind(self):
        store = OwnershipStore(bots_default="private", groups_default="shared")
        store.record("bots", BOT, principal())
        store.record("groups", GROUP, principal())
        self.assertEqual(store.get("bots", BOT).visibility, "private")
        self.assertEqual(store.get("groups", GROUP).visibility, "shared")

    def test_get_normalizes_identity_and_returns_none_when_unowned(self):
        store = OwnershipStore()
        store.record("bots", BOT, principal())
        self.assertIsNotNone(store.get("bots", BOT.upper()))
        self.assertIsNone(store.get("bots", OTHER_PLAYER))
        self.assertIsNone(store.get("groups", BOT))
        for kind in ("bot", "", None):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                store.get(kind, BOT)

    def test_set_visibility_requires_an_entry_and_validates_the_value(self):
        store = OwnershipStore(self.path)
        with self.assertRaises(ValueError):
            store.set_visibility("bots", BOT, "private")
        store.record("bots", BOT, principal())
        for visibility in VISIBILITIES:
            store.set_visibility("bots", BOT, visibility)
            self.assertEqual(store.get("bots", BOT).visibility, visibility)
        for visibility in ("public", "Shared", "", 1, None):
            with self.subTest(visibility=visibility), self.assertRaises(ValueError):
                store.set_visibility("bots", BOT, visibility)

    def test_set_owner_updates_or_creates_and_validates(self):
        store = OwnershipStore(bots_default="private")
        store.record("bots", BOT, principal())
        store.set_owner("bots", BOT, f"player:{OTHER_PLAYER}", "Bob")
        self.assertEqual(store.get("bots", BOT), Ownership(f"player:{OTHER_PLAYER}", "Bob", "private"))
        store.set_owner("groups", GROUP, f"xuid:{XUID}", None)
        self.assertEqual(store.get("groups", GROUP), Ownership(f"xuid:{XUID}", None, "shared"))
        for owner in ("", "xuid:", "player:", "uuid:1", 1, None, "x" * 129):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                store.set_owner("bots", BOT, owner, "Alice")
        for owner_name in ("x" * 65, 1, False):
            with self.subTest(owner_name=owner_name), self.assertRaises(ValueError):
                store.set_owner("bots", BOT, f"xuid:{XUID}", owner_name)
            self.assertEqual(store.get("bots", BOT), Ownership(f"player:{OTHER_PLAYER}", "Bob", "private"))

    def test_forget_removes_entries_and_writes_only_when_something_changed(self):
        store = OwnershipStore(self.path)
        store.record("bots", BOT, principal())
        store.record("groups", GROUP, principal())
        store.forget("bots", BOT.upper())
        self.assertIsNone(store.get("bots", BOT))
        self.assertIsNotNone(store.get("groups", GROUP))
        with patch.object(OwnershipStore, "_save") as save:
            store.forget("bots", BOT)
            store.forget("groups", OTHER_PLAYER)
        save.assert_not_called()

    def test_visible_to_truth_table(self):
        store = OwnershipStore()
        alice, console = principal(), Principal("console")
        bob = principal("Bob", xuid=OTHER_XUID, player_id=OTHER_PLAYER)
        store.record("bots", BOT, alice)
        for visibility, expected in (("shared", (True, True, True)), ("private", (True, False, True))):
            store.set_visibility("bots", BOT, visibility)
            with self.subTest(visibility=visibility):
                self.assertEqual(
                    (store.visible_to("bots", BOT, alice), store.visible_to("bots", BOT, bob),
                     store.visible_to("bots", BOT, console)),
                    expected,
                )
        self.assertTrue(store.visible_to("bots", OTHER_PLAYER, bob))
        store.record("groups", GROUP, alice)
        store.set_visibility("groups", GROUP, "private")
        self.assertTrue(store.visible_to("groups", GROUP, alice))
        self.assertFalse(store.visible_to("groups", GROUP, bob))

    def test_is_owner_truth_table(self):
        store = OwnershipStore()
        alice, console = principal(), Principal("console")
        bob = principal("Bob", xuid=OTHER_XUID, player_id=OTHER_PLAYER)
        self.assertTrue(store.is_owner("bots", BOT, bob))
        store.record("bots", BOT, alice)
        store.set_visibility("bots", BOT, "private")
        self.assertEqual(
            (store.is_owner("bots", BOT, alice), store.is_owner("bots", BOT, bob),
             store.is_owner("bots", BOT, console)),
            (True, False, True),
        )
        store.set_owner("bots", BOT, f"xuid:{OTHER_XUID}", "Bob")
        self.assertFalse(store.is_owner("bots", BOT, alice))
        self.assertTrue(store.is_owner("bots", BOT, bob))
        self.assertTrue(store.is_owner("bots", BOT, console))

    def test_file_round_trip_is_sorted_and_atomic(self):
        store = OwnershipStore(self.path, bots_default="private", groups_default="private")
        store.record("bots", BOT, principal())
        store.record("bots", "00000000-0000-4000-8000-0000000000ff", principal("Bob", xuid=OTHER_XUID))
        store.record("groups", GROUP, principal())
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        document = json.loads(text)
        self.assertEqual(document, {
            "version": 1,
            "bots": {
                BOT: {"owner": f"xuid:{XUID}", "ownerName": "Alice", "visibility": "private"},
                "00000000-0000-4000-8000-0000000000ff": {
                    "owner": f"xuid:{OTHER_XUID}", "ownerName": "Bob", "visibility": "private",
                },
            },
            "groups": {
                GROUP: {"owner": f"xuid:{XUID}", "ownerName": "Alice", "visibility": "private"},
            },
        })
        self.assertEqual(list(document["bots"]), sorted(document["bots"]))
        self.assertEqual(list(document["groups"]), sorted(document["groups"]))
        reloaded = OwnershipStore(self.path)
        self.assertEqual(reloaded.get("bots", BOT), Ownership(f"xuid:{XUID}", "Alice", "private"))
        self.assertEqual(reloaded.get("groups", GROUP), Ownership(f"xuid:{XUID}", "Alice", "private"))
        self.assertEqual(list(self.path.parent.glob(".ownership-*")), [])

    def test_memory_only_mode_writes_nothing(self):
        store = OwnershipStore(bots_default="private")
        store.record("bots", BOT, principal())
        self.assertEqual(store.get("bots", BOT).visibility, "private")
        store.set_visibility("bots", BOT, "shared")
        store.set_owner("groups", GROUP, f"xuid:{XUID}", "Alice")
        store.forget("bots", BOT)
        self.assertIsNone(store.path)
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_missing_file_and_missing_kinds_are_empty(self):
        self.assertEqual(load_ownership(self.path), {kind: {} for kind in KINDS})
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        self.assertEqual(load_ownership(self.path), {kind: {} for kind in KINDS})

    def test_invalid_files_are_refused(self):
        self.path.parent.mkdir(parents=True)
        duplicate = json.dumps({"version": 1, "bots": {BOT: entry(), BOT.upper(): entry()}})
        documents = (
            "",
            "{not json",
            "[]",
            json.dumps({"version": 2, "bots": {}, "groups": {}}),
            json.dumps({"bots": {}, "groups": {}}),
            json.dumps({"version": 1, "bots": {}, "groups": {}, "extra": True}),
            json.dumps({"version": 1, "bots": [], "groups": {}}),
            json.dumps({"version": 1, "bots": {}, "groups": None}),
            json.dumps({"version": 1, "bots": {BOT: []}}),
            json.dumps({"version": 1, "bots": {BOT: {**entry(), "extra": 1}}}),
            json.dumps({"version": 1, "bots": {BOT: {"ownerName": "Alice", "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": 1, "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": "xuid:", "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": "uuid:1", "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": "x" * 129, "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": f"xuid:{XUID}", "visibility": "shared",
                                                     "ownerName": "x" * 65}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": f"xuid:{XUID}", "visibility": "shared",
                                                     "ownerName": 1}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": f"xuid:{XUID}"}}}),
            json.dumps({"version": 1, "bots": {BOT: {"owner": f"xuid:{XUID}", "visibility": "public"}}}),
            json.dumps({"version": 1, "bots": {"not-a-uuid": entry()}}),
            json.dumps({"version": 1, "groups": {"not-a-uuid": entry()}}),
            duplicate,
        )
        for document in documents:
            with self.subTest(document=document[:80]):
                self.path.write_text(document, encoding="utf-8")
                with self.assertRaises((ValueError, TypeError)):
                    load_ownership(self.path)
                with self.assertRaises((ValueError, TypeError)):
                    OwnershipStore(self.path)

    def test_directory_in_place_of_the_file_is_refused(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(ValueError):
            load_ownership(self.path)
        with self.assertRaises(ValueError):
            OwnershipStore(self.path)

    def test_symlink_is_refused_for_reading_and_writing(self):
        store = OwnershipStore(self.path)
        target = self.make_symlink()
        with self.assertRaises(ValueError):
            load_ownership(self.path)
        with self.assertRaises(ValueError):
            store.record("bots", BOT, principal())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                         {"version": 1, "bots": {}, "groups": {}})
        self.assertEqual(list(self.path.parent.glob(".ownership-*")), [])

    def test_failed_write_keeps_the_memory_change_and_the_old_file(self):
        store = OwnershipStore(self.path)
        store.record("bots", BOT, principal())
        with (
            patch("endbot_core.ownership.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            store.record("groups", GROUP, principal())
        self.assertIsNotNone(store.get("groups", GROUP))
        self.assertIsNone(OwnershipStore(self.path).get("groups", GROUP))
        self.assertEqual(list(self.path.parent.glob(".ownership-*")), [])

    def test_invalid_identity_kind_and_defaults_are_refused(self):
        with self.assertRaises(ValueError):
            OwnershipStore(bots_default="private", groups_default="public")
        with self.assertRaises(ValueError):
            OwnershipStore(bots_default="Shared")
        store = OwnershipStore()
        for identity in ("", "not-a-uuid", None, 1):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                store.record("bots", identity, principal())
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                store.get("bots", identity)
        with self.assertRaises(ValueError):
            store.record("bot", BOT, principal())
        with self.assertRaises(ValueError):
            store.forget("bot", BOT)


if __name__ == "__main__":
    unittest.main()
