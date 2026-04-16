"""
src/scanner.py — Scanner wrapper with 2 modes:

- mock : uses built-in heuristics (same engine as secret_scanner.py), no subprocess
- test : invokes secret_scanner/secret_scanner.py via subprocess
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


class ScannerError(Exception):
    pass


# ---------------------------------------------------------------------------
# Shared scanning engine — used by mock mode and mirrors secret_scanner.py
# ---------------------------------------------------------------------------

_HEURISTICS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key",     re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key",     re.compile(r"AWS_SECRET_ACCESS_KEY\s*=\s*\S{6,}")),
    ("github_token",       re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.I)),
    ("bearer_token",       re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]{20,}", re.I)),
    ("base64_credentials", re.compile(r"Authorization:\s*Basic\s+[A-Za-z0-9+/=]{16,}", re.I)),
    ("generic_password",   re.compile(r'(?:password|passwd|pwd)\s*[=:\s"\']\s*\S{6,}', re.I)),
    ("generic_token",      re.compile(r'(?<!\w)(?:api[_-]token|auth[_-]token|access[_-]token|API_TOKEN|token)\s*[=:]\s*\S{6,}', re.I)),
    ("generic_api_key",    re.compile(r'(?:api[_-]?key|apikey)\s*[=:\s"\']\s*\S{6,}', re.I)),
    ("generic_secret",     re.compile(r'(?<!\w)(?:secret|private[_-]?key|client[_-]?secret)\s*=\s*\S{6,}', re.I)),
    ("generic_credential", re.compile(r'(?:credential|auth[_-]?key|service[_-]?key|app[_-]?secret)\s*[=:\s"\']\s*\S{6,}', re.I)),
]

_INFRA_RE = re.compile(
    r"##\[|Evaluating:|Requesting a runner|Waiting for|Job defined at|Current runner|add-mask",
    re.I
)


def _scan_text(log_text: str) -> list[dict]:
    """Scan log text and return raw findings (no repo/run context)."""
    findings = []
    current_step: str | None = None

    for line_no, line in enumerate(log_text.splitlines(), start=1):
        # Section headers injected by github_client: "=== Step Name ==="
        step_match = re.match(r"^===\s+(.+?)\s+===", line)
        if step_match:
            current_step = step_match.group(1).strip()
            continue

        # Strip ANSI escape codes
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)

        # Skip infrastructure lines
        if _INFRA_RE.search(line):
            continue

        # Skip shell command lines — output on the next line has the same value
        if re.search(r"\becho\b", line, re.I):
            continue

        for secret_type, pattern in _HEURISTICS:
            m = pattern.search(line)
            if m:
                findings.append({
                    "step":         current_step,
                    "line_number":  line_no,
                    "secret_type":  secret_type,
                    "matched_text": m.group(0),
                })
                break

    return findings


# ---------------------------------------------------------------------------
# Scanner class
# ---------------------------------------------------------------------------

class Scanner:
    VALID_MODES = {"mock", "test"}

    def __init__(self, mode: str = "mock", binary_path: Path | str | None = None):
        self._mode = (mode or "mock").strip().lower()
        if self._mode not in self.VALID_MODES:
            raise ScannerError(f"Invalid scanner mode '{mode}'. Valid values: mock, test.")
        self._binary_path = Path(binary_path) if binary_path else None
        self._cmd = self._build_cmd()

    @classmethod
    def from_config(cls, config: dict):
        mode = config.get("scanner_mode", "mock")
        scanner_path = config.get("scanner_path", "").strip() or None
        return cls(mode=mode, binary_path=scanner_path)

    def scan_log(self, log_text: str, repo: str, run_id: int, run_name: str) -> list[dict]:
        if self._mode == "mock":
            # Mock mode scans the log text directly — no temp file needed
            raw_findings = _scan_text(log_text)
            return self._enrich(raw_findings, repo, run_id, run_name)

        # test mode: write to a temp file and pass it to the scanner subprocess
        tmp_path = self._write_temp(log_text)
        try:
            raw_findings = self._invoke(tmp_path)
            return self._enrich(raw_findings, repo, run_id, run_name)
        finally:
            self._cleanup(tmp_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_cmd(self) -> list[str] | None:
        if self._mode == "mock":
            return None

        # test mode: default to secret_scanner/secret_scanner.py
        if self._binary_path is None:
            default_path = Path(__file__).resolve().parent.parent / "secret_scanner" / "secret_scanner.py"
            self._binary_path = default_path

        # If the path is relative, resolve it from the project root (src/..)
        # so it works correctly regardless of working directory
        if not self._binary_path.is_absolute():
            self._binary_path = (Path(__file__).resolve().parent.parent / self._binary_path).resolve()

        if not self._binary_path.exists():
            raise ScannerError(f"Scanner path does not exist: {self._binary_path}")

        if self._binary_path.suffix.lower() == ".py":
            return [sys.executable, str(self._binary_path)]

        return [str(self._binary_path)]

    def _invoke(self, tmp_path: str) -> list[dict]:
        if not self._cmd:
            raise ScannerError("Scanner command is not configured.")

        cmd = self._cmd + ["scan", tmp_path, "--format", "json"]
        print(f"[scanner:{self._mode}] running: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise ScannerError(f"Scanner timed out after 30s on {tmp_path}")
        except FileNotFoundError:
            raise ScannerError(f"Scanner binary not found: {self._cmd[0]}")

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            raise ScannerError(f"Scanner exited with code {result.returncode}: {stderr or stdout or 'no error output'}")

        try:
            raw = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as e:
            raise ScannerError(f"Failed to parse scanner output: {e}")

        findings = raw.get("findings", [])
        if not isinstance(findings, list):
            raise ScannerError("Scanner output JSON must contain a 'findings' list.")

        return findings

    @staticmethod
    def _write_temp(log_text: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(log_text)
            return tmp.name

    @staticmethod
    def _cleanup(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _enrich(raw_findings: list[dict], repo: str, run_id: int, run_name: str) -> list[dict]:
        return [
            {
                "repo":         repo,
                "run_id":       run_id,
                "run_name":     run_name,
                "step":         f.get("step"),
                "line_number":  f.get("line_number"),
                "secret_type":  f.get("secret_type"),
                "matched_text": f.get("matched_text"),
            }
            for f in raw_findings
        ]
