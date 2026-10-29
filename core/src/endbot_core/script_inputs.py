"""Track runtime input ownership for all callers, including Group batches.

All mutations and cleanup run on the ordered command worker. Poll renewals do
not acquire ownership. Cleanup uses UUID pins and individual input channels;
an operation by another caller replaces ownership only of the channels it sets.
"""

import threading
from contextlib import contextmanager

from endbot_core.control import RuntimeControlError


class OwnedInputs:
    def __init__(self, control):
        self.control = control
        self._local = threading.local()
        self._owners = {}

    @contextmanager
    def scope(self, run_id, check, pins=None):
        old = getattr(self._local, "scope", None)
        self._local.scope = (run_id, check, pins or {})
        try:
            yield
        finally:
            self._local.scope = old

    def request(self, method, **params):
        scope = getattr(self._local, "scope", None)
        if scope:
            scope[1]()  # Includes status/profile reads and every batched submission.
            name = params.get("name")
            if isinstance(name, str) and name.casefold() in scope[2] and "expectedIdentityId" not in params:
                params = {**params, "expectedIdentityId": scope[2][name.casefold()]}
            if method in {"move", "action", "flag", "mine", "stop"} and "expectedIdentityId" not in params:
                # Pin literal Bot targets too, before submitting a continuing input.
                # This also lets a lost acknowledgement retain cleanup ownership.
                identity = self.control.request("status", name=name)["identityId"]
                params = {**params, "expectedIdentityId": identity}
                scope[1]()
        try:
            result = self.control.request(method, **params)
        except RuntimeControlError as error:
            if scope and error.code in {"unavailable", "protocol"}:
                pending = params["requests"] if method == "groupInputBatch" else [{"method": method, "params": params}]
                for request in pending:
                    target = request["params"]
                    if target.get("expectedIdentityId"):
                        self._record(
                            request["method"],
                            target,
                            {"identityId": target["expectedIdentityId"], "name": target.get("name")},
                            scope,
                            uncertain=True,
                        )
            raise
        if method == "groupInputBatch":
            for request, response in zip(params["requests"], result["results"]):
                if response.get("ok"):
                    self._record(request["method"], request["params"], response["result"], scope)
        else:
            self._record(method, params, result, scope)
        return result

    def _record(self, method, params, result, scope, *, uncertain=False):
        if not isinstance(result, dict):
            return
        identity = result.get("identityId") or params.get("expectedIdentityId")
        if not identity:
            return
        if method == "mine" and params.get("mode") == "stop" and params.get("loopId"):
            current = self._owners.get((identity, "mine"))
            if current is not None and current[2] != params["loopId"]:
                return  # A stale mining poll's conditional stop cannot disown a newer job.
        channels = []
        if method == "move":
            channels = ["move"]
        elif method == "action":
            channels = [params["action"]]
        elif method == "flag":
            channels = [params["flag"]]
        elif method == "mine" and params.get("mode") != "continue":
            channels = ["mine"]
            if params.get("mode") != "stop" and not uncertain:
                for cleared in ("move", "attack", "use"):
                    self._owners.pop((identity, cleared), None)
        elif method in {"stop", "despawn", "reconnect", "forget", "rename"}:
            channels = ["move", "jump", "attack", "use", "sprint", "sneak", "mine"]
        owner = scope[0] if scope else None
        if (
            method in {"stop", "despawn", "reconnect", "forget", "rename"}
            or (method == "move" and params.get("direction") == "stop")
            or (
                method == "action"
                and (params.get("mode") == "stop" or (params.get("mode") == "once" and "holdTicks" not in params))
            )
            or (method == "flag" and not params.get("value"))
            or (method == "mine" and params.get("mode") == "stop")
        ):
            owner = None
        if uncertain and owner is None:
            return  # An unacknowledged stop/one-shot cannot prove an earlier input ended.
        for channel in channels:
            if owner is None:
                self._owners.pop((identity, channel), None)
            else:
                self._owners[(identity, channel)] = (
                    owner,
                    result.get("name", params.get("name")),
                    (result.get("mining") or {}).get("loopId"),
                )

    def cleanup(self, run_id):
        failures = []
        for (identity, channel), entry in tuple(self._owners.items()):
            if entry[0] != run_id:
                continue
            self._owners.pop((identity, channel), None)
            params = {"name": entry[1], "expectedIdentityId": identity}
            method = "action"
            if channel == "move":
                method, params["direction"] = "move", "stop"
            elif channel == "mine":
                method, params["mode"] = "mine", "stop"
                if entry[2]:
                    params["loopId"] = entry[2]
            elif channel in {"sprint", "sneak"}:
                method, params["flag"], params["value"] = "flag", channel, False
            else:
                params.update(action=channel, mode="stop")
            try:
                self.control.request(method, **params)
            except (RuntimeError, ValueError) as error:
                failures.append(str(error))
                if isinstance(error, RuntimeControlError) and error.code == "unavailable":
                    # One bounded transport failure is enough; do not block ordinary
                    # commands behind a timeout for every member/channel in the run.
                    for key, owned in tuple(self._owners.items()):
                        if owned[0] == run_id:
                            self._owners.pop(key, None)
                    break
        return failures
