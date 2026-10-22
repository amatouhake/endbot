"""Startup-only scripting settings. Frame bounds are protocol correctness limits."""

from dataclasses import dataclass, fields
from pathlib import Path

FRAME_BYTES = 256 * 1024
SOURCE_BYTES = 64 * 1024  # Leaves room for JSON escaping, bootstrap and operation table.


@dataclass(frozen=True)
class ScriptConfig:
    enabled: bool = False
    runner: str = ""
    controller_runs: int = 2
    server_runs: int = 8
    wall_seconds: int = 300
    slice_ms: int = 100
    interrupt_budget: int = 1000000
    operations_per_second: int = 20
    max_script_bytes: int = 32768
    max_scripts: int = 64
    memory_bytes: int = 32 * 1024 * 1024

    @classmethod
    def from_config(cls, config):
        # Endstone loads config.toml with tomlkit, whose integers are not plain int.
        if hasattr(config, "unwrap"):
            config = config.unwrap()
        if not isinstance(config, dict):
            raise ValueError("scripting must be a table")  # noqa: TRY004 - configuration validation API
        values = {key.replace("-", "_"): value for key, value in config.items()}
        if values.keys() - {f.name for f in fields(cls)}:
            raise ValueError("Unknown scripting setting")
        result = cls(**values)
        if type(result.enabled) is not bool or not isinstance(result.runner, str):
            raise ValueError("Invalid scripting enabled/runner setting")
        for f in fields(cls):
            if f.name in {"enabled", "runner"}:
                continue
            value = getattr(result, f.name)
            if type(value) is not int or not 1 <= value <= 2147483647:
                raise ValueError(f"scripting.{f.name} must be a positive 32-bit integer")
        if result.max_script_bytes > SOURCE_BYTES or result.memory_bytes < 1024 * 1024:
            raise ValueError("Invalid scripting source/memory protocol bounds")
        return result

    def executable(self, data_folder):
        if self.runner:
            path = Path(self.runner)
            return path if path.is_absolute() else Path(data_folder) / path
        import sys

        return (
            Path(__file__).parent
            / "runner"
            / ("endbot-luau-spike.exe" if sys.platform == "win32" else "endbot-luau-spike")
        )

    def limits(self):
        return {
            "wallMs": self.wall_seconds * 1000,
            "sliceMs": self.slice_ms,
            "interruptBudget": self.interrupt_budget,
            "memoryBytes": self.memory_bytes,
            "maxSourceBytes": self.max_script_bytes,
        }
