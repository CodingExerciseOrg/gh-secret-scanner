"""
src/identity_registry.py — Manages per-identity Storage and Poller instances.

Each identity hash gets its own:
  - Storage  → config/users/<hash>/
  - Poller   → background daemon thread
  - Scanner  → built from the identity's config

Idle poller reaping:
  Any identity whose poller has not been accessed within IDLE_TIMEOUT_SECONDS
  is considered abandoned. A background reaper thread checks every
  REAPER_INTERVAL_SECONDS and stops+removes idle pollers to avoid
  accumulating stale threads from browsers that have moved on.

  The status endpoint (polled every 3 s by active browsers) calls
  touch_identity() on every request, so any open browser tab keeps
  its poller alive automatically.
"""

import threading
import time
from pathlib import Path

from storage import Storage
from scanner import Scanner
from poller  import Poller

# A poller is considered idle if not accessed for 2 × the poll interval.
# Default poll interval is 30 min, so idle timeout is 60 min.
IDLE_TIMEOUT_SECONDS  = 60 * 60   # 1 hour
REAPER_INTERVAL_SECONDS = 5 * 60  # check every 5 minutes


class IdentityRegistry:

    def __init__(self, base_dir: Path, on_update_factory):
        """
        base_dir         : root config directory (config/)
        on_update_factory: callable(identity_hash) → on_update(msg) fn
        """
        self._base_dir          = base_dir
        self._on_update_factory = on_update_factory
        self._lock              = threading.RLock()
        self._storages: dict[str, Storage] = {}
        self._pollers:  dict[str, Poller]  = {}
        self._last_seen: dict[str, float]  = {}   # identity → last access timestamp

        # Start background reaper
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="PollerReaper")
        self._reaper.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_storage(self, identity: str) -> Storage:
        with self._lock:
            if identity not in self._storages:
                data_dir = self._base_dir / "users" / identity
                self._storages[identity] = Storage(data_dir)
            return self._storages[identity]

    def get_poller(self, identity: str) -> Poller:
        with self._lock:
            if identity not in self._pollers:
                storage = self.get_storage(identity)
                config  = storage.load_config()
                scanner = Scanner.from_config(config)
                poller  = Poller(
                    storage=storage,
                    scanner=scanner,
                    on_update=self._on_update_factory(identity),
                )
                poller.start()
                self._pollers[identity] = poller
            return self._pollers[identity]

    def touch_identity(self, identity: str) -> None:
        """Record that this identity was just accessed. Resets its idle timer."""
        with self._lock:
            self._last_seen[identity] = time.monotonic()

    def rebuild_scanner(self, identity: str) -> None:
        """Rebuild scanner from current config and push to the poller."""
        with self._lock:
            storage = self.get_storage(identity)
            config  = storage.load_config()
            scanner = Scanner.from_config(config)
            if identity in self._pollers:
                self._pollers[identity].set_scanner(scanner)
                self._pollers[identity].reset_auth_state()
                self._pollers[identity].trigger_now()

    def stop_all(self) -> None:
        """Stop all pollers cleanly on server shutdown."""
        with self._lock:
            for poller in self._pollers.values():
                poller.stop()
            for poller in self._pollers.values():
                poller.join(timeout=10)
            self._pollers.clear()

    # ------------------------------------------------------------------
    # Idle reaper
    # ------------------------------------------------------------------

    def _reap_loop(self) -> None:
        """Background thread: stop pollers that have been idle too long."""
        while True:
            time.sleep(REAPER_INTERVAL_SECONDS)
            self._reap_idle()

    def _reap_idle(self) -> None:
        now = time.monotonic()
        to_stop: list[str] = []

        with self._lock:
            for ident, poller in self._pollers.items():
                last = self._last_seen.get(ident, 0)
                if now - last > IDLE_TIMEOUT_SECONDS:
                    to_stop.append(ident)

        for ident in to_stop:
            print(f"[registry] stopping idle poller for identity {ident[:8]}…")
            with self._lock:
                poller = self._pollers.pop(ident, None)
            if poller:
                poller.stop()
                poller.join(timeout=10)
