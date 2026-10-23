import unittest

from plain_service import PlainCommandService

BOT = {"identityId": "00000000-0000-4000-8000-000000000001", "name": "Alice", "desiredState": "online",
       "connectionState": "online"}
OBSERVED = {"dimension": "Overworld", "x": 1.5, "y": 64.0, "z": -2.5, "yaw": 90.0, "pitch": 10.0, "health": 18.0,
            "maxHealth": 20.0, "onGround": True, "inWater": False, "selectedSlot": 2,
            "held": {"name": "minecraft:wheat", "count": 5}}
SLOTS = [{"slot": "2", "name": "minecraft:wheat", "count": 5}, {"slot": "12", "name": "minecraft:stone", "count": 64},
         {"slot": "head", "name": "minecraft:iron_helmet", "count": 1}]


class Control:
    def __init__(self):
        self.calls = []

    def request(self, method, **params):
        self.calls.append((method, params))
        status = {**BOT, "vitals": {"hunger": 17.0, "saturation": 4.5}}
        if method == "hotbar":
            return {**status, "selectedHotbarSlot": params.get("slot", 2)}
        if method == "entities":
            return {**status, "entities": [{"runtimeId": "20", "type": "minecraft:cow", "distance": 2.1}]}
        return status


class World:
    def observe(self, identity_id):
        return OBSERVED

    def inventory(self, name, identity_id=None):
        return {"name": "Alice", "slots": SLOTS}

    def describe_entities(self, identity_id, runtime_ids):
        return {"20": {"position": {"x": 3.0, "y": 64.0, "z": 0.5}, "health": 10.0, "maxHealth": 10.0,
                       "nameTag": None}}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.service = PlainCommandService(Control(), World())

    def test_status_data_has_position_look_and_vitals(self):
        result = self.service.execute(["Alice", "status"])
        data = result.data
        self.assertEqual(data["position"], {"x": 1.5, "y": 64.0, "z": -2.5})
        self.assertEqual((data["dimension"], data["look"]), ("Overworld", {"yaw": 90.0, "pitch": 10.0}))
        self.assertEqual((data["health"], data["maxHealth"], data["hunger"], data["saturation"]),
                         (18.0, 20.0, 17.0, 4.5))
        self.assertEqual((data["onGround"], data["inWater"], data["selectedSlot"]), (True, False, 2))
        self.assertEqual(data["held"], {"name": "minecraft:wheat", "count": 5})
        self.assertIn("health 18/20 food 17", result.messages[0])

    def test_status_data_without_a_world_observation_is_nil(self):
        data = PlainCommandService(Control()).execute(["Alice", "status"]).data
        self.assertIsNone(data["position"])
        self.assertIsNone(data["health"])
        self.assertEqual(data["hunger"], 17.0)

    def test_inventory_and_hotbar_return_slots(self):
        self.assertEqual(self.service.execute(["Alice", "inventory"]).data, {"name": "Alice", "slots": SLOTS})
        hotbar = self.service.execute(["Alice", "hotbar"]).data
        self.assertEqual(hotbar, {"name": "Alice", "selectedSlot": 2, "slots": [SLOTS[0]]})
        self.assertNotIn("slots", self.service.execute(["Alice", "hotbar", "3"]).data)

    def test_entities_include_bds_details(self):
        entity = self.service.execute(["Alice", "entities"]).data["entities"][0]
        self.assertEqual(entity["position"], {"x": 3.0, "y": 64.0, "z": 0.5})
        self.assertEqual((entity["type"], entity["health"], entity["nameTag"]), ("minecraft:cow", 10.0, None))

    def test_japanese_status_vitals(self):
        class Japanese:
            language = "ja"

        message = self.service.execute(["Alice", "status"], Japanese()).messages[0]
        self.assertIn("体力 18/20 満腹度 17", message)


if __name__ == "__main__":
    unittest.main()
