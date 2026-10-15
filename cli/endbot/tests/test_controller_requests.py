import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from endbot_cli.controller_requests import (
    REQUEST_DIR,
    SESSION_FILE,
    STALE_REQUEST_SECONDS,
    controller_request,
    dispatch_requests,
    prune_requests,
)
from endbot_cli.fsutil import atomic_write_text


class ControllerRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)
        self.session = str(uuid4())
        (self.run_dir / "supervisor.pid").write_text(str(os.getpid()))
        (self.run_dir / SESSION_FILE).write_text(json.dumps({"session": self.session}))

    def reply(self, line, *, ok=True, wrong_id=False):
        request_id = line.split()[-1]
        directory = self.run_dir / REQUEST_DIR
        path = directory / f"{request_id}.dispatch.json"
        request = json.loads(path.read_text())
        result = {
            "version": 1,
            "id": str(uuid4()) if wrong_id else request_id,
            "session": self.session,
            "ok": ok,
            "messages": [f"{request['target']}: {'allowed' if ok else 'denied'}"],
            "entries": [],
        }
        atomic_write_text(directory / f"{request_id}.response.json", json.dumps(result))
        path.unlink()
        return True

    def test_concurrent_requests_get_only_their_own_result(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(controller_request, self.run_dir, "add", name, timeout=2)
                for name in ("First Player", "Second Player")
            ]
            deadline = time.monotonic() + 2
            while not all(future.done() for future in futures) and time.monotonic() < deadline:
                dispatch_requests(self.run_dir, self.session, self.reply)
                time.sleep(0.01)
            self.assertEqual(
                [future.result()["messages"] for future in futures],
                [["First Player: allowed"], ["Second Player: allowed"]],
            )
        self.assertEqual(list((self.run_dir / REQUEST_DIR).iterdir()), [])

    def test_failed_result_is_not_reported_as_success(self):
        thread = threading.Thread(target=lambda: self._dispatch_one(lambda line: self.reply(line, ok=False)))
        thread.start()
        result = controller_request(self.run_dir, "remove", "Missing", timeout=2)
        thread.join(2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["messages"], ["Missing: denied"])

    def _dispatch_one(self, send):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if list((self.run_dir / REQUEST_DIR).glob("*.request.json")):
                dispatch_requests(self.run_dir, self.session, send)
                return
            time.sleep(0.01)

    def test_timeout_cancels_undispatched_request_and_reports_unknown(self):
        with self.assertRaisesRegex(ValueError, "result is unknown"):
            controller_request(self.run_dir, "add", "Owner", timeout=0.05)
        self.assertEqual(list((self.run_dir / REQUEST_DIR).iterdir()), [])

    def test_dispatched_mutation_is_never_automatically_retried(self):
        calls = []
        thread = threading.Thread(target=lambda: self._dispatch_one(lambda line: calls.append(line) or True))
        thread.start()
        with self.assertRaisesRegex(ValueError, "result is unknown"):
            controller_request(self.run_dir, "add", "Owner", timeout=0.15)
        thread.join(2)
        dispatch_requests(self.run_dir, self.session, lambda line: calls.append(line) or True)
        self.assertEqual(len(calls), 1)

    def test_abandoned_dispatch_and_late_response_files_are_pruned(self):
        directory = self.run_dir / REQUEST_DIR
        directory.mkdir()
        old, fresh = str(uuid4()), str(uuid4())
        for request_id in (old, fresh):
            for suffix in ("dispatch", "response"):
                (directory / f"{request_id}.{suffix}.json").write_text("{}")
        stale = time.time() - STALE_REQUEST_SECONDS - 1
        for suffix in ("dispatch", "response"):
            os.utime(directory / f"{old}.{suffix}.json", (stale, stale))
        dispatch_requests(self.run_dir, self.session, lambda line: True)
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         [f"{fresh}.dispatch.json", f"{fresh}.response.json"])
        prune_requests(self.run_dir)
        self.assertEqual(list(directory.iterdir()), [])

    def test_stale_session_cannot_apply_an_old_mutation(self):
        directory = self.run_dir / REQUEST_DIR
        directory.mkdir()
        request_id = str(uuid4())
        (directory / f"{request_id}.request.json").write_text(
            json.dumps({"version": 1, "id": request_id, "session": str(uuid4()), "operation": "add", "target": "Owner"})
        )
        calls = []
        dispatch_requests(self.run_dir, self.session, lambda line: calls.append(line) or True)
        self.assertEqual(calls, [])
        self.assertFalse(json.loads((directory / f"{request_id}.response.json").read_text())["ok"])

    def test_request_never_turns_target_into_console_code(self):
        for target in ("Owner\nstop", "Owner\rstop", "", "   "):
            with self.assertRaises(ValueError):
                controller_request(self.run_dir, "add", target, timeout=0.01)
        self.assertFalse((self.run_dir / REQUEST_DIR).exists())

    def test_mismatched_response_is_rejected(self):
        thread = threading.Thread(target=lambda: self._dispatch_one(lambda line: self.reply(line, wrong_id=True)))
        thread.start()
        with self.assertRaisesRegex(ValueError, "did not match"):
            controller_request(self.run_dir, "list", timeout=2)
        thread.join(2)
