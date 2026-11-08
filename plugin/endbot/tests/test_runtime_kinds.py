"""Runtime- and endbotd-sourced event kinds (BOT-022 step 6b, EVENTS.md E4, E9).

The profile poll is driven directly and through its thread; job.finished and server.started are driven through the
real OccurrenceHub, with WatchBuffers standing in for a run's watch.
"""

import time
import unittest

from endbot_core.events import Event, reason
from endbot_core.operations import EVENTS
from endbot_core.principal import Principal
from endbot_core.watches import WatchBuffer, validate_filter
from endbotd.occurrences import OccurrenceHub
from endbotd.runtime_kinds import RuntimeKinds


def wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakeControl:
    """A runtime control that answers only the profile list and counts its calls."""

    def __init__(self, profiles=None):
        self.profiles = list(profiles or [])
        self.calls = 0

    def request(self, method, **parameters):
        if method != "profiles":
            raise AssertionError(f"unexpected runtime request {method!r}")
        self.calls += 1
        return [dict(profile) for profile in self.profiles]


def profile(identity, name, state):
    return {"identityId": identity, "name": name, "connectionState": state}


class RuntimeKindTests(unittest.TestCase):
    def setUp(self):
        self.control = FakeControl()
        self.hub = OccurrenceHub(EVENTS, lambda interest: None)
        self.kinds = RuntimeKinds(self.hub, self.control, log=lambda level, message: None)

    def watch(self, kind, filter_=None):
        buffer = WatchBuffer(kind)
        subscription = self.hub.subscribe(kind, filter_ or {}, buffer.on_item, buffer.on_gap)
        self.addCleanup(self.hub.unsubscribe, subscription)
        return buffer

    def item(self, buffer):
        result = buffer.wait(0, lambda: None)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def empty(self, buffer):
        self.assertEqual(buffer.wait(0, lambda: None)["code"], "timeout")

    def test_the_first_poll_is_a_baseline_and_online_and_offline_transitions_are_reported(self):
        online, offline = self.watch("bot.online"), self.watch("bot.offline")
        self.control.profiles = [profile("1", "Miner", "online")]
        self.assertTrue(self.kinds.poll())
        self.empty(online)
        self.empty(offline)

        self.kinds.poll()  # unchanged
        self.empty(online)
        self.empty(offline)

        self.control.profiles = [profile("1", "Miner", "offline")]
        self.kinds.poll()
        self.empty(online)
        self.assertEqual(self.item(offline),
                         {"kind": "bot.offline", "bot": "Miner", "reason": "offline"})

        self.control.profiles = [profile("1", "Miner", "online")]
        self.kinds.poll()
        self.assertEqual(self.item(online), {"kind": "bot.online", "bot": "Miner"})
        self.empty(offline)

        self.control.profiles = []  # the profile was removed while online
        self.kinds.poll()
        self.empty(online)
        self.assertEqual(self.item(offline),
                         {"kind": "bot.offline", "bot": "Miner", "reason": "removed"})

    def test_a_bot_that_was_never_online_raises_no_offline(self):
        offline = self.watch("bot.offline")
        self.control.profiles = [profile("1", "Miner", "offline")]
        self.kinds.poll()
        self.kinds.poll()
        self.empty(offline)

    def test_the_bot_filter_limits_which_bot_is_delivered(self):
        online = self.watch("bot.online", {"bot": "Miner"})
        self.control.profiles = [profile("1", "Miner", "offline"), profile("2", "Builder", "online")]
        self.kinds.poll()
        self.control.profiles = [profile("1", "Miner", "online"), profile("2", "Builder", "online")]
        self.kinds.poll()
        self.assertEqual(self.item(online), {"kind": "bot.online", "bot": "Miner"})
        self.empty(online)

    def test_polling_stops_without_interest_and_restarts_with_a_baseline(self):
        self.control.profiles = [profile("1", "Miner", "online")]
        self.assertFalse(self.kinds.poll())
        self.assertEqual(self.control.calls, 0)
        buffer = WatchBuffer("bot.online")
        subscription = self.hub.subscribe("bot.online", {}, buffer.on_item, buffer.on_gap)
        self.assertTrue(self.kinds.poll())
        self.assertEqual(self.control.calls, 1)
        self.empty(buffer)
        self.hub.unsubscribe(subscription)
        self.assertFalse(self.kinds.poll())
        self.assertEqual(self.control.calls, 1)
        # A new interest is a new baseline, even though the Bot was already online.
        online = self.watch("bot.online")
        self.assertTrue(self.kinds.poll())
        self.empty(online)
        self.assertEqual(self.control.calls, 2)

    def test_the_monitor_thread_polls_only_while_a_runtime_kind_is_watched(self):
        monitor = RuntimeKinds(self.hub, self.control, poll_seconds=0.01, idle_seconds=0.01).start()
        self.addCleanup(monitor.close)
        time.sleep(0.05)
        self.assertEqual(self.control.calls, 0)
        self.watch("bot.online")
        self.assertTrue(wait_for(lambda: self.control.calls >= 1))
        monitor.close()
        calls = self.control.calls
        time.sleep(0.05)
        self.assertEqual(self.control.calls, calls)

    def test_a_runtime_failure_is_a_new_baseline_not_a_burst_of_offline_reports(self):
        offline = self.watch("bot.offline")
        self.control.profiles = [profile("1", "Miner", "online")]
        self.kinds.poll()
        original = self.control.request

        def broken(method, **parameters):
            raise RuntimeError("runtime restarted")

        self.control.request = broken
        self.kinds.poll()
        self.control.request = original
        self.control.profiles = [profile("1", "Miner", "online")]
        self.kinds.poll()
        # The outage is reported once as a gap, never as a burst of offline reports.
        result = offline.wait(0, lambda: None)
        self.assertEqual((result["code"], result["reason"]), ("unavailable", "source restarted"))
        self.empty(offline)

    def test_a_watch_made_after_the_last_sample_gets_no_change_that_may_predate_it(self):
        early = self.watch("bot.online")
        time.sleep(0.03)  # the Windows monotonic clock advances in steps of about 16 ms
        self.control.profiles = [profile("1", "Miner", "offline")]
        self.kinds.poll()
        time.sleep(0.03)
        self.control.profiles = [profile("1", "Miner", "online")]
        late = self.watch("bot.online")  # made after the sample: the connect may have happened before it
        self.kinds.poll()
        self.assertEqual(self.item(early)["bot"], "Miner")
        self.empty(late)

    def test_job_ended_becomes_job_finished_with_its_bot_kind_and_reason(self):
        finished = self.watch("job.finished")
        self.kinds.on_event(Event(Principal("console"), "job.ended", {
            "bot": "Miner", "job": "destroy continuous", "reason": reason("disconnected")}))
        self.assertEqual(self.item(finished), {"kind": "job.finished", "bot": "Miner", "job": "mining",
                                               "reason": "disconnected"})
        self.kinds.on_event(Event(Principal("console"), "job.ended", {
            "bot": "Miner", "job": "move", "reason": "duration elapsed"}))
        self.assertEqual(self.item(finished), {"kind": "job.finished", "bot": "Miner", "job": "move",
                                               "reason": "duration elapsed"})
        self.kinds.on_event(Event(Principal("console"), "job.waiting", {"bot": "Miner", "blockType": None}))
        self.kinds.on_event(Event(Principal("console"), "job.ended", {"bot": "", "job": "move", "reason": "x"}))
        self.empty(finished)

    def test_the_job_filter_limits_the_job_kind(self):
        finished = self.watch("job.finished", {"job": "mining"})
        self.kinds.on_event(Event(Principal("console"), "job.ended", {
            "bot": "Miner", "job": "move", "reason": "stopped"}))
        self.kinds.on_event(Event(Principal("console"), "job.ended", {
            "bot": "Miner", "job": "destroy", "reason": "stopped"}))
        self.assertEqual(self.item(finished), {"kind": "job.finished", "bot": "Miner", "job": "mining",
                                               "reason": "stopped"})
        self.empty(finished)

    def test_server_started_is_raised_once_per_boot_and_not_by_a_reconnect(self):
        started = self.watch("server.started")
        self.hub.link_changed("epoch-1", boot="boot-1")
        self.assertEqual(self.item(started), {"kind": "server.started"})
        self.empty(started)
        self.hub.link_changed(None, closed="epoch-1")
        self.hub.link_changed("epoch-2", boot="boot-1")  # the same BDS process reconnects
        self.empty(started)
        self.hub.link_changed(None, closed="epoch-2")
        self.hub.link_changed("epoch-3", boot="boot-2")  # BDS restarted: a new plugin process
        self.assertEqual(self.item(started), {"kind": "server.started"})
        self.empty(started)

    def test_every_new_kind_reaches_a_watch_through_the_hub(self):
        buffers = {kind: self.watch(kind)
                   for kind in ("bot.online", "bot.offline", "bot.died", "job.finished", "server.started")}
        self.control.profiles = [profile("1", "Miner", "offline")]
        self.kinds.poll()  # the baseline
        self.control.profiles = [profile("1", "Miner", "online")]
        self.kinds.poll()
        self.assertEqual(self.item(buffers["bot.online"]), {"kind": "bot.online", "bot": "Miner"})
        self.control.profiles = []
        self.kinds.poll()
        self.assertEqual(self.item(buffers["bot.offline"]),
                         {"kind": "bot.offline", "bot": "Miner", "reason": "removed"})
        self.hub.raise_occurrence("plugin", {"kind": "bot.died", "bot": "Miner",
                                             "message": "death.attack.generic"})
        self.assertEqual(self.item(buffers["bot.died"]),
                         {"kind": "bot.died", "bot": "Miner", "message": "death.attack.generic"})
        self.kinds.on_event(Event(Principal("console"), "job.ended", {
            "bot": "Miner", "job": "destroy", "reason": reason("Bot disconnected")}))
        self.assertEqual(self.item(buffers["job.finished"]),
                         {"kind": "job.finished", "bot": "Miner", "job": "mining", "reason": "Bot disconnected"})
        self.hub.link_changed("epoch-1", boot="boot-1")
        self.assertEqual(self.item(buffers["server.started"]), {"kind": "server.started"})

    def test_the_registry_filters_validate_for_the_new_kinds(self):
        for kind in ("bot.online", "bot.offline", "bot.died", "job.finished", "server.started"):
            with self.subTest(kind=kind):
                self.assertEqual(validate_filter(kind, {}, ()), (kind, {}))
        self.assertEqual(validate_filter("bot.online", {"bot": "Miner"}, ()),
                         ("bot.online", {"bot": "Miner"}))
        self.assertEqual(validate_filter("job.finished", {"bot": "Miner", "job": "mining"}, ()),
                         ("job.finished", {"bot": "Miner", "job": "mining"}))


if __name__ == "__main__":
    unittest.main()
