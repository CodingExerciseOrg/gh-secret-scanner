"""
src/identity_registry.py — Manages per-identity Storage and Poller instances.

Each identity hash gets its own:
  - Storage  → config/users/<hash>/
  - Poller   → background daemon thread
  - Scanner  → built from the identity's config

Instances are created lazily on first access and stopped on shutdown.
"""

import threading
from pathlib import Path

from storage import Storage
from scanner import Scanner
from poller  import Poller


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
