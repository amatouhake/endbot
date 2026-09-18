import unittest
from uuid import UUID

from endstone_endbot.registry import BotIdentity, BotRegistry


class BotRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = BotRegistry()
        self.identity = BotIdentity(UUID("63572362-0c83-5f0a-8cec-e1b788101798"), "Miner1")

    def test_hidden_uuid_is_the_primary_identity(self) -> None:
        self.registry.register(self.identity)
        self.assertIs(self.registry.get_by_id(self.identity.identity_id), self.identity)
        self.assertIs(self.registry.get_by_name("miner1"), self.identity)

    def test_names_and_uuids_are_unique(self) -> None:
        self.registry.register(self.identity)
        with self.assertRaisesRegex(ValueError, "UUID"):
            self.registry.register(BotIdentity(self.identity.identity_id, "Builder1"))
        with self.assertRaisesRegex(ValueError, "name"):
            self.registry.register(BotIdentity(UUID("526290d1-a78e-4a18-b32e-8c0d14e10850"), "MINER1"))


if __name__ == "__main__":
    unittest.main()

