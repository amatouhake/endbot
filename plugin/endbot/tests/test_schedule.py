"""The ``schedule`` event kind (BOT-022 step 6c, EVENTS.md E4, E9, S3).

Filters are validated through the registry; the timer is driven directly with an injected clock,
so no test sleeps. Delivery goes through a real OccurrenceHub into a WatchBuffer.
"""

import unittest
from datetime import datetime

from endbot_core.i18n import LocalizedError
from endbot_core.operations import EVENTS
from endbot_core.watches import WatchBuffer, parse_duration, parse_time, validate_filter
from endbotd.occurrences import OccurrenceHub
from endbotd.schedule import KIND, ScheduleTimer, next_time


def local(year, month, day, hour, minute, second=0):
    """An epoch timestamp for a local wall-clock time."""
    return datetime(year, month, day, hour, minute, second).timestamp()


class Clock:
    """An injected wall clock; only a test advances it."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FilterTests(unittest.TestCase):
    def test_durations_are_an_integer_and_a_unit_from_ten_seconds_to_a_day(self):
        for value, seconds in (("10s", 10), ("30s", 30), ("90s", 90), ("1m", 60), ("90m", 5400),
                               ("2h", 7200), ("24h", 86400), ("1440m", 86400)):
            with self.subTest(value=value):
                self.assertEqual(parse_duration(value), seconds)
                self.assertEqual(validate_filter("schedule", {"every": value}, ()),
                                 ("schedule", {"every": value}))

    def test_anything_else_than_a_duration_in_range_is_refused_with_the_field_and_form(self):
        for value in ("9s", "86401s", "25h", "0s", "1.5s", "1d", "1M", "10", " 10s", "10s ", "-10s", "", None, 10):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_duration(value)
                with self.assertRaises(LocalizedError) as raised:
                    validate_filter("schedule", {"every": value}, ())
                message = str(raised.exception)
                self.assertIn("every", message)
                self.assertIn("10s to 24h", message)

    def test_times_are_hh_mm_in_local_time(self):
        for value, hour_minute in (("00:00", (0, 0)), ("08:05", (8, 5)), ("12:34", (12, 34)),
                                   ("23:59", (23, 59))):
            with self.subTest(value=value):
                self.assertEqual(parse_time(value), hour_minute)
                self.assertEqual(validate_filter("schedule", {"at": value}, ()),
                                 ("schedule", {"at": value}))

    def test_anything_else_than_hh_mm_is_refused_with_the_field_and_form(self):
        for value in ("24:00", "12:60", "9:00", "12:3", "12:00:00", "1230", " 12:00", "", None, 1200):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_time(value)
                with self.assertRaises(LocalizedError) as raised:
                    validate_filter("schedule", {"at": value}, ())
                message = str(raised.exception)
                self.assertIn("at", message)
                self.assertIn("HH:MM", message)

    def test_a_schedule_needs_every_or_at_and_knows_no_other_filter(self):
        with self.assertRaisesRegex(LocalizedError, "needs a filter: every or at"):
            validate_filter("schedule", {}, ())
        self.assertEqual(validate_filter("schedule", {"every": "1m", "at": "09:00"}, ()),
                         ("schedule", {"every": "1m", "at": "09:00"}))
        with self.assertRaisesRegex(LocalizedError, "has no filter phase"):
            validate_filter("schedule", {"phase": "night"}, ())

    def test_a_world_time_phase_is_one_of_the_four_phases(self):
        for phase in ("day", "sunset", "night", "sunrise"):
            with self.subTest(phase=phase):
                self.assertEqual(validate_filter("world.time", {"phase": phase}, ()),
                                 ("world.time", {"phase": phase}))
        self.assertEqual(validate_filter("world.time", {}, ()), ("world.time", {}))
        with self.assertRaisesRegex(LocalizedError, "phase must be one of day, sunset, night, sunrise"):
            validate_filter("world.time", {"phase": "dusk"}, ())

    def test_the_next_time_is_strictly_after_now(self):
        now = datetime(2026, 11, 9, 8, 0, 0)
        self.assertEqual(next_time(now, 9, 0), datetime(2026, 11, 9, 9, 0))
        self.assertEqual(next_time(now, 8, 0), datetime(2026, 11, 10, 8, 0))
        self.assertEqual(next_time(datetime(2026, 11, 9, 8, 59, 59), 9, 0), datetime(2026, 11, 9, 9, 0))


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.interests = []
        self.hub = OccurrenceHub(EVENTS, self.interests.append)
        self.clock = Clock(local(2026, 11, 9, 8, 0, 0))
        self.timer = ScheduleTimer(self.hub, clock=self.clock, log=lambda level, message: None)

    def watch(self, filter_):
        buffer = WatchBuffer(KIND)
        subscription = self.hub.subscribe(KIND, filter_, buffer.on_item, buffer.on_gap)
        self.addCleanup(self.hub.unsubscribe, subscription)
        return buffer

    def item(self, buffer):
        result = buffer.wait(0, lambda: None)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def empty(self, buffer):
        self.assertEqual(buffer.wait(0, lambda: None)["code"], "timeout")

    def test_every_counts_from_the_subscription_and_fires_once_per_interval(self):
        buffer = self.watch({"every": "10s"})
        self.assertEqual(self.timer.poll(), 0)  # adopts the subscription at 08:00:00
        self.clock.advance(9)
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(1)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-09T08:00:10"})
        self.empty(buffer)
        self.clock.advance(9)
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(1)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-09T08:00:20"})
        self.assertEqual(self.timer.scheduled, 1)

    def test_at_fires_once_a_day_at_that_minute(self):
        buffer = self.watch({"at": "09:00"})
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(3599)
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(1)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-09T09:00:00"})
        self.empty(buffer)
        self.clock.advance(23 * 3600 + 3599)
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(1)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-10T09:00:00"})
        self.empty(buffer)

    def test_a_subscription_made_after_the_time_waits_for_tomorrow(self):
        self.clock.now = local(2026, 11, 9, 10, 0, 0)
        buffer = self.watch({"at": "09:00"})
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(22 * 3600 + 3599)  # 08:59:59 the next day
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(1)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-10T09:00:00"})

    def test_no_schedule_is_kept_or_fired_without_a_subscription(self):
        self.assertEqual(self.timer.poll(), 0)
        self.assertEqual((self.timer.scheduled, self.interests), (0, []))
        self.clock.advance(2 * 86400)
        self.assertEqual(self.timer.poll(), 0)
        buffer = self.watch({"every": "1m"})
        self.assertEqual(self.timer.poll(), 0)  # the baseline: the interval has not passed
        self.assertEqual(self.timer.scheduled, 1)
        self.clock.advance(59)
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(1)
        self.assertEqual(self.timer.poll(), 1)  # still the only subscription
        self.item(buffer)

    def test_a_gone_subscription_is_forgotten(self):
        buffer = self.watch({"every": "10s"})
        self.timer.poll()
        self.assertEqual(self.timer.scheduled, 1)
        for subscription in self.hub.subscriptions(KIND):
            self.hub.unsubscribe(subscription)
        self.clock.advance(60)
        self.assertEqual(self.timer.poll(), 0)
        self.assertEqual(self.timer.scheduled, 0)
        self.empty(buffer)

    def test_each_due_subscription_receives_only_its_own_occurrence(self):
        fast = self.watch({"every": "10s"})
        slow = self.watch({"every": "30s"})
        self.assertEqual(self.timer.poll(), 0)
        self.clock.advance(10)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(fast), {"kind": KIND, "at": "2026-11-09T08:00:10"})
        self.empty(slow)
        self.clock.advance(10)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(fast), {"kind": KIND, "at": "2026-11-09T08:00:20"})
        self.empty(slow)
        self.clock.advance(10)
        self.assertEqual(self.timer.poll(), 2)
        self.assertEqual(self.item(fast), {"kind": KIND, "at": "2026-11-09T08:00:30"})
        self.assertEqual(self.item(slow), {"kind": KIND, "at": "2026-11-09T08:00:30"})

    def test_a_schedule_is_raised_while_the_plugin_link_is_down(self):
        # S3: schedule is endbotd-sourced, so it is never part of the plugin's interest set, and
        # OccurrenceHub.link_changed was never called (BDS is stopped).
        self.assertEqual(self.hub.interest(), {"revision": 0, "block": [], "kinds": []})
        buffer = self.watch({"every": "10s"})
        self.timer.poll()
        self.clock.advance(10)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-09T08:00:10"})

    def test_a_late_pass_fires_once_and_skips_missed_slots(self):
        buffer = self.watch({"every": "10s"})
        self.timer.poll()
        self.clock.advance(35)
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-09T08:00:10"})
        self.empty(buffer)
        self.clock.advance(5)  # 08:00:40, the next future slot
        self.assertEqual(self.timer.poll(), 1)
        self.assertEqual(self.item(buffer), {"kind": KIND, "at": "2026-11-09T08:00:40"})


if __name__ == "__main__":
    unittest.main()
