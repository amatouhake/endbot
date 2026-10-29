"""Synthetic controller identities only; no accounts or tokens are used."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endbot_core.grant_revisions import GrantRevisions, load_grant_revisions

UUID = "0000000a-0000-4000-8000-00000000000b"
OTHER_UUID = "00000000-0000-4000-8000-000000000002"


class GrantRevisionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state" / "grant-revisions.json"

    def test_missing_file_counts_every_revision_as_zero(self):
        revisions = GrantRevisions(self.path)
        self.assertEqual((revisions.get(UUID), revisions.get(OTHER_UUID)), (0, 0))
        self.assertFalse(self.path.exists())

    def test_bump_persists_a_minimal_versioned_document_across_restart(self):
        revisions = GrantRevisions(self.path)
        revisions.bump([UUID, UUID])
        revisions.bump([UUID, OTHER_UUID])
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            {"version": 1, "revisions": {UUID: 2, OTHER_UUID: 1}},
        )
        reloaded = GrantRevisions(self.path)
        self.assertEqual((reloaded.get(UUID), reloaded.get(OTHER_UUID)), (2, 1))
        self.assertEqual(list(self.path.parent.glob(".grant-revisions-*")), [])

    def test_empty_bump_writes_nothing(self):
        GrantRevisions(self.path).bump([])
        self.assertFalse(self.path.exists())

    def test_uppercase_identity_is_canonicalized(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1, "revisions": {UUID.upper(): 3}}), encoding="utf-8")
        self.assertEqual(GrantRevisions(self.path).get(UUID), 3)

    def test_corrupt_file_is_refused(self):
        self.path.parent.mkdir(parents=True)
        documents = (
            "{not json",
            "[]",
            json.dumps({"version": 2, "revisions": {}}),
            json.dumps({"revisions": {UUID: 1}}),
            json.dumps({"version": 1, "revisions": {UUID: 1}, "extra": True}),
            json.dumps({"version": 1, "revisions": [UUID]}),
            json.dumps({"version": 1, "revisions": {"not-a-uuid": 1}}),
            json.dumps({"version": 1, "revisions": {UUID: 0}}),
            json.dumps({"version": 1, "revisions": {UUID: "1"}}),
            json.dumps({"version": 1, "revisions": {UUID: True}}),
            json.dumps({"version": 1, "revisions": {UUID: 1, UUID.upper(): 2}}),
        )
        for document in documents:
            with self.subTest(document=document):
                self.path.write_text(document, encoding="utf-8")
                with self.assertRaises((ValueError, TypeError)):
                    load_grant_revisions(self.path)

    def test_directory_in_place_of_the_file_is_refused(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(ValueError):
            load_grant_revisions(self.path)

    def test_failed_write_still_bumps_in_memory_and_keeps_the_old_file(self):
        revisions = GrantRevisions(self.path)
        revisions.bump([UUID])
        with (
            patch("endbot_core.grant_revisions.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            revisions.bump([UUID])
        self.assertEqual(revisions.get(UUID), 2)
        self.assertEqual(GrantRevisions(self.path).get(UUID), 1)
        self.assertEqual(list(self.path.parent.glob(".grant-revisions-*")), [])


if __name__ == "__main__":
    unittest.main()
