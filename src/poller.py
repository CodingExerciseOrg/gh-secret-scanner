"""
src/poller.py — Background thread that polls GitHub every N minutes
                for new workflow runs and scans their logs.

Authentication is delegated to auth.make_client() which handles both
token and GitHub App auth, including automatic token refresh.
"""

import threading
import time
from typing import Callable

from auth import make_client
from github_client import GitHubError
from scanner import Scanner, ScannerError
from storage import Storage


POLL_INTERVAL_SECONDS = 30 * 60


class Poller(threading.Thread):
    """
    Daemon thread that polls GitHub for new workflow runs and scans their logs.
    """

    def __init__(
        self,
        storage: Storage,
        scanner: Scanner,
        on_update: Callable[[str], None],
    ):
        super().__init__(daemon=True)
        self._storage     = storage
        self._scanner     = scanner
        self._on_update   = on_update
        self._stop_event  = threading.Event()
        self._force_event = threading.Event()
        self._next_poll_at = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()
        self._force_event.set()

    def trigger_now(self) -> None:
        """Force an immediate poll cycle (e.g. when config is saved)."""
        self._force_event.set()

    def set_scanner(self, scanner: Scanner) -> None:
        """Replace scanner instance at runtime."""
        self._scanner = scanner

    def reset_auth_state(self) -> None:
        """
        Force a fresh token exchange on the next poll cycle.
        Called when config changes so stale app tokens are never reused.
        """
        cfg = self._storage.load_config()
        cfg["token_expires_at"] = 0
        self._storage.save_config(cfg)

    def seconds_until_next_poll(self) -> int:
        remaining = int(self._next_poll_at - time.time())
        return max(0, remaining)

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_cycle()
            except Exception as e:
                self._on_update(f" Poller crashed unexpectedly: {e}. Will retry next cycle.")
            self._next_poll_at = time.time() + POLL_INTERVAL_SECONDS

            while not self._stop_event.is_set():
                timeout = max(0.0, self._next_poll_at - time.time())
                triggered = self._force_event.wait(timeout=timeout)
                if triggered:
                    self._force_event.clear()
                    break
                if time.time() >= self._next_poll_at:
                    break

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _poll_cycle(self) -> None:
        config = self._storage.load_config()
        org    = config.get("org", "").strip()

        if not org:
            self._on_update("⚠ Poller skipped: no org configured.")
            return

        try:
            client = make_client(self._storage)
        except GitHubError as e:
            self._on_update(f" Auth error: {e}")
            return

        self._on_update(f" Polling {org} for new workflow runs…")

        try:
            repos = client.list_repos(org)
        except GitHubError as e:
            self._on_update(f" GitHub error: {e}")
            return

        seen            = self._storage.load_seen_runs()
        new_findings    : list[dict] = []
        processed_count = 0

        for repo_data in repos:
            repo_name = repo_data["name"]
            try:
                runs = client.list_recent_runs(org, repo_name)
                print(f"[poller] {repo_name}: {len(runs)} runs found")
            except GitHubError:
                continue

            for run in runs:
                run_id = run["id"]
                key    = f"{org}/{repo_name}/{run_id}"
                if key in seen:
                    continue

                self._on_update(f" Scanning {repo_name} run #{run_id}…")

                try:
                    log_text = client.download_logs(org, repo_name, run_id)
                except GitHubError as e:
                    self._on_update(f"  ⚠ Log download failed: {e}")
                    continue

                if not log_text:
                    if run.get("status") == "completed":
                        seen.add(key)
                        self._on_update(
                            f"  ℹ No logs for completed run #{run_id}; marking as processed."
                        )
                    else:
                        self._on_update(
                            f"  ℹ Run #{run_id} not ready yet; will retry next poll."
                        )
                    continue

                try:
                    findings = self._scanner.scan_log(
                        log_text,
                        repo=f"{org}/{repo_name}",
                        run_id=run_id,
                        run_name=run.get("name", ""),
                    )
                except ScannerError as e:
                    self._on_update(f"   Scanner error: {e}")
                    continue

                seen.add(key)
                processed_count += 1

                print(f"[poller] run #{run_id} ({repo_name}): {len(findings)} findings")
                if findings:
                    new_findings.extend(findings)
                    self._on_update(
                        f"   {len(findings)} finding(s) in {repo_name} run #{run_id}"
                    )

        self._storage.save_seen_runs(seen)
        if new_findings:
            self._storage.append_findings(new_findings)

        self._on_update(
            f"Poll complete. {processed_count} new run(s) processed, "
            f"{len(new_findings)} finding(s) added."
        )
