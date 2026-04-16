"""
src/storage.py — Per-identity JSON file persistence.

Each identity's data lives under config/users/<hash>/.
The Storage instance is always constructed with an explicit data_dir —
the caller (main.py / routes) is responsible for resolving the correct
directory from the identity cookie.
"""

import json
import threading
from pathlib import Path
from typing import Any


class Storage:
    DEFAULT_CONFIG = {
        "org":              "",
        "auth_method":      "token",
        "token":            "",
        "app_id":           "",
        "installation_id":  "",
        "private_key":      "",
        "token_expires_at": 0,
        "scanner_mode":     "mock",
        "scanner_path":     "",
    }

    def __init__(self, data_dir: Path):
        self._dir  = data_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Generic load / save
    # ------------------------------------------------------------------

    def load(self, name: str, default: Any = None) -> Any:
        with self._lock:
            p = self._path(name)
            if not p.exists():
                return default
            try:
                with p.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return default

    def save(self, name: str, data: Any) -> None:
        with self._lock:
            p   = self._path(name)
            tmp = p.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(p)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def load_config(self) -> dict:
        return self.load("config", dict(self.DEFAULT_CONFIG))

    def save_config(self, config: dict) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config)
        self.save("config", merged)

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------

    def load_findings(self) -> list[dict]:
        return self.load("findings", [])

    def append_findings(self, new_findings: list[dict]) -> None:
        with self._lock:
            existing = self.load_findings()
            existing_keys = {
                (f["repo"], f["run_id"], f.get("step"), f["line_number"], f["secret_type"])
                for f in existing
            }
            for f in new_findings:
                key = (f["repo"], f["run_id"], f.get("step"), f["line_number"], f["secret_type"])
                if key not in existing_keys:
                    existing.append(f)
                    existing_keys.add(key)
            self.save("findings", existing)

    def clear_findings(self) -> None:
        self.save("findings", [])

    # ------------------------------------------------------------------
    # Seen runs
    # ------------------------------------------------------------------

    def load_seen_runs(self) -> set[str]:
        return set(self.load("seen_runs", []))

    def save_seen_runs(self, seen: set[str]) -> None:
        self.save("seen_runs", list(seen))

    def clear_seen_runs(self) -> None:
        self.save("seen_runs", [])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path(self, name: str) -> Path:
        return self._dir / f"{name}.json"
