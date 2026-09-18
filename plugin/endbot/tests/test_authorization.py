import unittest

from endstone_endbot.authorization import AllowedPlayers


class AllowedPlayersTests(unittest.TestCase):
    def test_empty_configuration_is_fail_closed(self) -> None:
        self.assertFalse(AllowedPlayers.from_config({}).allows("63572362-0c83-5f0a-8cec-e1b788101798", "123"))

    def test_uuid_or_xuid_can_explicitly_grant_control(self) -> None:
        allowed = AllowedPlayers.from_config(
            {
                "allowed-uuids": ["63572362-0C83-5F0A-8CEC-E1B788101798"],
                "allowed-xuids": ["2535469543141592"],
            }
        )
        self.assertTrue(allowed.allows("63572362-0c83-5f0a-8cec-e1b788101798", ""))
        self.assertTrue(allowed.allows("526290d1-a78e-4a18-b32e-8c0d14e10850", "2535469543141592"))
        self.assertFalse(allowed.allows("526290d1-a78e-4a18-b32e-8c0d14e10850", "7"))

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AllowedPlayers.from_config({"allowed-uuids": ["not-a-uuid"]})
        with self.assertRaises(TypeError):
            AllowedPlayers.from_config({"allowed-xuids": [1234]})
        with self.assertRaises(TypeError):
            AllowedPlayers.from_config({"allowed-uuids": "not-an-array"})
