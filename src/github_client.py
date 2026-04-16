"""
src/github_client.py — Thin wrapper around the GitHub REST API.

Supports two authentication methods:
  - Token auth  : a classic PAT or fine-grained token with repo / actions:read scopes.
  - App auth    : a GitHub App installation token generated from App ID, Installation ID
                  and a private key (.pem). The token is valid for 1 hour; callers should
                  refresh it by calling GitHubClient.from_app() again before expiry.
"""

import io
import re
import time
import zipfile
from pathlib import Path

import requests

API = "https://api.github.com"
BASE_HEADERS = {
    "Accept":               "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHubError(Exception):
    pass


class GitHubClient:
    """
    Authenticated GitHub REST API client.
    Handles pagination transparently on list operations.
    """

    def __init__(self, token: str):
        if not token:
            raise GitHubError("No authentication token provided.")
        self._session = requests.Session()
        self._session.headers.update({
            **BASE_HEADERS,
            "Authorization": f"Bearer {token}",
        })

    # ------------------------------------------------------------------
    # GitHub App factory
    # ------------------------------------------------------------------

    @classmethod
    def from_app(cls, app_id: str, installation_id: str, private_key: str) -> tuple["GitHubClient", str]:
        """
        Authenticate as a GitHub App installation.

        Steps:
          1. Sign a JWT with the App's private key (valid 9 minutes).
          2. Exchange the JWT for an installation access token (valid 1 hour).
          3. Return a (GitHubClient, token) tuple so callers can persist the token.

        Parameters:
            app_id          : GitHub App's numeric ID (string).
            installation_id : Installation ID for the target org.
            private_key     : PEM-encoded RSA private key as a string,
                              or a path to a .pem file.
        """
        try:
            import jwt as pyjwt
        except ImportError:
            raise GitHubError(
                "PyJWT is required for GitHub App auth. "
                "Run: pip install PyJWT cryptography"
            )

        # Accept either a PEM string or a file path
        pem = private_key.strip()
        if not pem.startswith("-----"):
            # Treat as a file path
            pem_path = Path(pem)
            if not pem_path.exists():
                raise GitHubError(f"Private key file not found: {pem_path}")
            pem = pem_path.read_text(encoding="utf-8").strip()

        # Step 1: Generate JWT
        now = int(time.time())
        payload = {
            "iat": now - 60,       # issued slightly in the past to allow clock skew
            "exp": now + 540,      # 9 minutes (GitHub max is 10)
            "iss": str(app_id),
        }
        try:
            jwt_token = pyjwt.encode(payload, pem, algorithm="RS256")
        except Exception as e:
            raise GitHubError(f"Failed to sign JWT: {e}")

        # Step 2: Exchange JWT for installation access token
        url = f"{API}/app/installations/{installation_id}/access_tokens"
        r = requests.post(
            url,
            headers={**BASE_HEADERS, "Authorization": f"Bearer {jwt_token}"},
        )
        if not r.ok:
            try:
                msg = r.json().get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise GitHubError(f"GitHub App token exchange failed ({r.status_code}): {msg}")

        token = r.json().get("token", "")
        if not token:
            raise GitHubError("GitHub App token exchange returned no token.")

        # Return both the client and the raw token so callers can persist it
        return cls(token=token), token

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    def list_repos(self, org: str) -> list[dict]:
        """Return all repos visible to the token for an org."""
        repos = []
        url = f"{API}/orgs/{org}/repos"
        params = {"per_page": 100, "type": "all"}
        while url:
            r = self._get(url, params=params)
            repos.extend(r.json())
            url = r.links.get("next", {}).get("url")
            params = {}
        return repos

    # ------------------------------------------------------------------
    # Workflow runs
    # ------------------------------------------------------------------

    def list_recent_runs(self, org: str, repo: str, limit: int = 10) -> list[dict]:
        """Return the most recent workflow runs for a repository."""
        url = f"{API}/repos/{org}/{repo}/actions/runs"
        r = self._get(url, params={"per_page": limit})
        return r.json().get("workflow_runs", [])

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def download_logs(self, org: str, repo: str, run_id: int) -> str:
        """
        Download the log archive for a run and return its contents as a
        single concatenated string, one section per job/step.

        Returns an empty string if logs have expired or the run is still
        in progress.
        """
        url = f"{API}/repos/{org}/{repo}/actions/runs/{run_id}/logs"
        r = self._session.get(url, allow_redirects=True)

        if r.status_code == 404:
            return ""

        self._raise_for_status(r)

        parts = []
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            all_names = sorted(zf.namelist())
            print(f"[github_client] zip files: {all_names}")

            sub_level = [n for n in all_names if n.endswith(".txt") and "/" in n]
            top_level = [n for n in all_names if n.endswith(".txt") and "/" not in n]

            _INFRA_STEPS = re.compile(
                r"^(Set up job|Complete job|Post\b|system)",
                re.I
            )
            useful_sub = [
                n for n in sub_level
                if not _INFRA_STEPS.match(re.sub(r"^\d+_", "", n.split("/")[-1].replace(".txt", "")))
            ]
            selected = useful_sub if useful_sub else top_level
            print(f"[github_client] using {'per-step' if useful_sub else 'merged'} files ({len(selected)} total)")

            for name in selected:
                content = zf.read(name).decode("utf-8", errors="replace")
                if "/" in name:
                    base = name.split("/")[-1]
                    base = re.sub(r"\.txt$", "", base)
                    step = re.sub(r"^\d+_", "", base)
                    parts.append(f"=== {step} ===\n{content}")
                else:
                    parts.append(content)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, **kwargs) -> requests.Response:
        r = self._session.get(url, **kwargs)
        self._raise_for_status(r)
        return r

    @staticmethod
    def _raise_for_status(r: requests.Response) -> None:
        if not r.ok:
            try:
                msg = r.json().get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise GitHubError(f"GitHub API {r.status_code}: {msg}")
