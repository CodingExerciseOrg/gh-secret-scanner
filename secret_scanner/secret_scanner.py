#!/usr/bin/env python3
"""
secret_scanner/secret_scanner.py — Standalone secret-scanning binary.

Usage:
    python secret_scanner.py scan <log_file> --format json

Output (stdout):
    JSON with findings. Each finding contains:
        - line_number  : int
        - secret_type  : str
        - matched_text : str
        - step         : str | null  (derived from === section headers in the log)

Self-contained — no dependencies on the rest of the app.
Compile to a standalone executable with:
    pyinstaller --onefile secret_scanner.py
"""

import argparse
import json
import re
import sys


class SecretScanner:
    """
    Scans a log file for secrets using regex heuristics.
    Prints JSON to stdout — identical interface to what a real binary would provide.
    """

    HEURISTICS: list[tuple[str, re.Pattern]] = [
        # Specific well-known formats — checked first (most precise)
        ("aws_access_key",     re.compile(r"AKIA[0-9A-Z]{16}")),
        ("aws_secret_key",     re.compile(r"AWS_SECRET_ACCESS_KEY\s*=\s*\S{6,}")),
        ("github_token",       re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.I)),
        ("bearer_token",       re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]{20,}", re.I)),
        ("base64_credentials", re.compile(r"Authorization:\s*Basic\s+[A-Za-z0-9+/=]{16,}", re.I)),

        # Password variants
        ("generic_password",   re.compile(r'(?:password|passwd|pwd)\s*[=:\s"\']\s*\S{6,}', re.I)),

        # Token variants — require explicit label, word boundary prevents matching mid-word
        ("generic_token",      re.compile(r'(?<!\w)(?:api[_-]token|auth[_-]token|access[_-]token|API_TOKEN|token)\s*[=:]\s*\S{6,}', re.I)),

        # API key variants
        ("generic_api_key",    re.compile(r'(?:api[_-]?key|apikey)\s*[=:\s"\']\s*\S{6,}', re.I)),

        # Secret variants — require = not just any separator (avoids "Secret source: Actions")
        ("generic_secret",     re.compile(r'(?<!\w)(?:secret|private[_-]?key|client[_-]?secret)\s*=\s*\S{6,}', re.I)),

        # Catch-all credential labels
        ("generic_credential", re.compile(r'(?:credential|auth[_-]?key|service[_-]?key|app[_-]?secret)\s*[=:\s"\']\s*\S{6,}', re.I)),
    ]

    # GitHub Actions runner infrastructure lines — never contain real secrets
    _INFRA_RE = re.compile(
        r"##\[|Evaluating:|Requesting a runner|Waiting for|Job defined at|Current runner|add-mask",
        re.I
    )

    VERSION = "1.0.0"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def scan(self, path: str) -> dict:
        """
        Scan a file and return the full JSON-serialisable result dict.
        Raises FileNotFoundError or OSError if the file cannot be read.
        """
        findings = self._scan_file(path)
        return {
            "scanner_version": self.VERSION,
            "source_file":     path,
            "findings":        findings,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_file(self, path: str) -> list[dict]:
        findings = []
        current_step: str | None = None

        with open(path, encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):

                # Section headers injected by github_client: "=== Step Name ==="
                step_match = re.match(r"^===\s+(.+?)\s+===", line)
                if step_match:
                    current_step = step_match.group(1).strip()
                    continue

                # Strip ANSI escape codes from runner output
                line = re.sub(r"\x1b\[[0-9;]*m", "", line)

                # Skip GitHub Actions runner infrastructure lines
                if self._INFRA_RE.search(line):
                    continue

                # Skip shell command lines e.g. `echo "api_key=..."` —
                # the actual output on the following line has the same value
                if re.search(r"\becho\b", line, re.I):
                    continue

                for secret_type, pattern in self.HEURISTICS:
                    m = pattern.search(line)
                    if m:
                        findings.append({
                            "line_number":  line_no,
                            "secret_type":  secret_type,
                            "matched_text": m.group(0),
                            "step":         current_step,
                        })
                        break  # one finding per line is enough

        return findings

    @staticmethod
    def _redact(value: str) -> str:
        return value


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scan a log file for secrets.")
    subparsers = parser.add_subparsers(dest="command")

    scan_cmd = subparsers.add_parser("scan", help="Scan a log file.")
    scan_cmd.add_argument("log_file", help="Path to the .txt log file to scan.")
    scan_cmd.add_argument("--format", choices=["json"], default="json",
                          help="Output format (only json supported).")

    args = parser.parse_args()

    if args.command != "scan":
        parser.print_help()
        sys.exit(1)

    scanner = SecretScanner()

    try:
        result = scanner.scan(args.log_file)
    except FileNotFoundError:
        print(json.dumps({"error": f"File not found: {args.log_file}", "findings": []}))
        sys.exit(1)
    except OSError as e:
        print(json.dumps({"error": str(e), "findings": []}))
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()