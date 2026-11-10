"""The trigger policy state machine (BOT-022 5a, EVENTS E8, E9) at explicit times."""

import unittest

from endbot_core.trigger_policy import Decision, TriggerPolicy


def policy(**changes) -> TriggerPolicy:
    values = {"concurrency": "skip", "cooldown": 20, "startsPerMinute": 10, "queue": 4}
    values.update(changes)
    return TriggerPolicy(values)


class SkipPolicyTest(unittest.TestCase):
    def test_occurrences_during_a_run_are_missed_and_counted(self):
        trigger = policy()
        self.assertEqual(trigger.offer(0.0), Decision("start", 0, None))
        trigger.started(0.0)
        for now in (0.5, 1.0, 1.5):
            self.assertEqual(trigger.offer(now), Decision("missed", 0, None))
        self.assertEqual(trigger.active, 1)
        self.assertEqual(trigger.pending, 0)
        self.assertEqual(trigger.missed_total, 3)

    def test_occurrences_in_cooldown_are_missed(self):
        trigger = policy()
        trigger.offer(0.0)
        trigger.started(0.0)
        trigger.finished(0.1)
        self.assertEqual(trigger.offer(0.5), Decision("missed", 0, None))
        self.assertEqual(trigger.missed_total, 1)
        self.assertEqual(trigger.offer(1.0), Decision("start", 1, None))
        trigger.started(1.0)
        self.assertEqual(trigger.missed_total, 1)

    def test_missed_is_reported_once_on_the_next_start_and_reset(self):
        trigger = policy()
        trigger.offer(10.0)
        trigger.started(10.0)
        trigger.finished(10.2)
        for now in (10.4, 10.6):
            trigger.offer(now)
        self.assertEqual(trigger.missed_total, 2)
        self.assertEqual(trigger.offer(11.0), Decision("start", 2, None))
        trigger.started(11.0)
        trigger.finished(11.2)
        self.assertEqual(trigger.offer(12.0), Decision("start", 0, None))

    def test_cooldown_is_ticks_over_twenty(self):
        trigger = policy(cooldown=1)
        trigger.offer(0.0)
        trigger.started(0.0)
        trigger.finished(0.0)
        self.assertEqual(trigger.offer(0.049), Decision("missed", 0, None))
        self.assertEqual(trigger.offer(0.05), Decision("start", 1, None))

    def test_due_only_starts_queued_occurrences(self):
        trigger = policy()
        self.assertIsNone(trigger.due(0.0))
        trigger.offer(0.0)
        trigger.started(0.0)
        trigger.finished(1.0)
        self.assertIsNone(trigger.due(1.0))


class QueuePolicyTest(unittest.TestCase):
    def test_occurrences_queue_and_start_in_order_through_due(self):
        trigger = policy(concurrency="queue", queue=2)
        trigger.offer(0.0)
        trigger.started(0.0)
        self.assertEqual(trigger.offer(0.1), Decision("queued", 0, None))
        self.assertEqual(trigger.offer(0.2), Decision("queued", 0, None))
        self.assertEqual(trigger.pending, 2)
        self.assertIsNone(trigger.due(0.5))
        trigger.finished(1.0)
        self.assertIsNone(trigger.due(0.99))
        self.assertEqual(trigger.due(1.0), Decision("start", 0, None))
        trigger.started(1.0)
        self.assertEqual(trigger.pending, 1)
        self.assertIsNone(trigger.due(1.5))
        trigger.finished(2.0)
        self.assertEqual(trigger.due(2.0), Decision("start", 0, None))
        trigger.started(2.0)
        self.assertEqual(trigger.pending, 0)
        self.assertIsNone(trigger.due(3.0))

    def test_queue_overflow_drops_the_oldest_and_counts_it_missed(self):
        trigger = policy(concurrency="queue", queue=1)
        trigger.offer(0.0)
        trigger.started(0.0)
        trigger.offer(0.1)
        trigger.offer(0.2)
        self.assertEqual(trigger.pending, 1)
        self.assertEqual(trigger.missed_total, 1)
        trigger.finished(1.0)
        self.assertEqual(trigger.due(1.0), Decision("start", 1, None))
        trigger.started(1.0)
        self.assertEqual(trigger.missed_total, 1)
        self.assertEqual(trigger.pending, 0)

    def test_an_occurrence_does_not_start_while_a_queue_is_waiting(self):
        trigger = policy(concurrency="queue", queue=2)
        trigger.offer(0.0)
        trigger.started(0.0)
        trigger.offer(0.1)
        trigger.finished(1.0)
        self.assertEqual(trigger.offer(1.0), Decision("queued", 0, None))
        self.assertEqual(trigger.pending, 2)
        self.assertEqual(trigger.due(1.0), Decision("start", 0, None))


class ParallelPolicyTest(unittest.TestCase):
    def test_at_most_two_runs_and_the_third_is_missed(self):
        trigger = policy(concurrency="parallel", cooldown=20)
        self.assertEqual(trigger.offer(0.0), Decision("start", 0, None))
        trigger.started(0.0)
        self.assertEqual(trigger.offer(1.0), Decision("start", 0, None))
        trigger.started(1.0)
        self.assertEqual(trigger.offer(1.5), Decision("missed", 0, None))
        self.assertEqual(trigger.offer(2.0), Decision("missed", 0, None))
        self.assertEqual(trigger.active, 2)
        self.assertEqual(trigger.missed_total, 2)

    def test_cooldown_still_separates_parallel_starts(self):
        trigger = policy(concurrency="parallel", cooldown=20)
        trigger.offer(0.0)
        trigger.started(0.0)
        self.assertEqual(trigger.offer(0.5), Decision("missed", 0, None))
        self.assertEqual(trigger.offer(1.0), Decision("start", 1, None))
        trigger.started(1.0)
        trigger.finished(1.5)
        self.assertEqual(trigger.offer(1.9), Decision("missed", 0, None))
        self.assertEqual(trigger.offer(2.0), Decision("start", 1, None))


class RateLimitTest(unittest.TestCase):
    def test_ten_starts_between_zero_and_fifty_nine_seconds_are_allowed(self):
        trigger = policy(cooldown=0)
        for second in range(10):
            self.assertEqual(trigger.offer(float(second)), Decision("start", 0, None))
            trigger.started(float(second))
            trigger.finished(float(second))
        self.assertEqual(trigger.missed_total, 0)

    def test_the_eleventh_start_in_the_window_is_dropped_not_disabled(self):
        trigger = policy(cooldown=0)
        for second in range(10):
            trigger.offer(float(second))
            trigger.started(float(second))
            trigger.finished(float(second))
        self.assertEqual(trigger.offer(59.0), Decision("missed", 0, "rate limit"))
        self.assertTrue(trigger.throttle_started())
        self.assertEqual(trigger.offer(59.5), Decision("missed", 0, "rate limit"))
        self.assertFalse(trigger.throttle_started())  # reported once per episode
        self.assertEqual(trigger.offer(60.0), Decision("start", 2, None))
        self.assertFalse(trigger.throttle_started())

    def test_the_window_frees_after_sixty_seconds(self):
        trigger = policy(cooldown=0)
        for _ in range(10):
            trigger.offer(0.0)
            trigger.started(0.0)
            trigger.finished(0.0)
        self.assertEqual(trigger.offer(60.0), Decision("start", 0, None))

    def test_a_new_throttle_episode_is_reported_again_after_a_start(self):
        trigger = policy(cooldown=0, startsPerMinute=1)
        for now in (0.0, 60.0):
            self.assertEqual(trigger.offer(now).action, "start")
            trigger.started(now)
            trigger.finished(now)
            self.assertEqual(trigger.offer(now + 1).reason, "rate limit")
            self.assertTrue(trigger.throttle_started())

    def test_a_queued_occurrence_over_the_window_waits_for_it(self):
        trigger = policy(concurrency="queue", cooldown=0, startsPerMinute=1)
        trigger.offer(0.0)
        trigger.started(0.0)
        self.assertEqual(trigger.offer(0.1), Decision("queued", 0, None))
        trigger.finished(0.1)
        self.assertIsNone(trigger.due(0.2))
        self.assertTrue(trigger.throttle_started())
        self.assertEqual(trigger.pending, 1)
        self.assertEqual(trigger.due(60.0), Decision("start", 0, None))

    def test_a_queue_trigger_over_the_window_queues_new_occurrences(self):
        trigger = policy(concurrency="queue", cooldown=0, startsPerMinute=1)
        trigger.offer(0.0)
        trigger.started(0.0)
        trigger.finished(0.0)
        self.assertEqual(trigger.offer(1.0), Decision("queued", 0, "rate limit"))
        self.assertEqual(trigger.pending, 1)


class RestartAndGapTest(unittest.TestCase):
    def test_restored_last_start_holds_the_cooldown(self):
        trigger = TriggerPolicy({"concurrency": "skip", "cooldown": 20}, last_start=100.0)
        self.assertEqual(trigger.offer(100.5), Decision("missed", 0, None))
        self.assertEqual(trigger.offer(101.0), Decision("start", 1, None))

    def test_restored_last_start_does_not_fill_the_minute_window(self):
        trigger = TriggerPolicy({"concurrency": "skip", "cooldown": 0, "startsPerMinute": 1},
                                last_start=100.0)
        self.assertEqual(trigger.offer(101.0), Decision("start", 0, None))

    def test_skipped_down_counts_as_missed(self):
        trigger = policy()
        trigger.skipped_down()
        trigger.skipped_down()
        self.assertEqual(trigger.missed_total, 2)
        self.assertEqual(trigger.offer(0.0), Decision("start", 2, None))
        trigger.started(0.0)
        trigger.finished(0.5)
        trigger.skipped_down()
        self.assertEqual(trigger.missed_total, 3)
        self.assertEqual(trigger.offer(1.0), Decision("start", 1, None))

    def test_memory_counters_start_at_zero(self):
        trigger = policy()
        self.assertEqual((trigger.active, trigger.pending, trigger.missed_total), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
