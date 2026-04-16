"""
run.py — Launcher for local development and deployment.

Usage:
    python run.py

Changes the working directory to src/ before starting uvicorn so all
imports resolve as flat module names (e.g. 'storage', 'scanner').
This works correctly on Windows where sys.path changes don't carry
over to uvicorn's spawned subprocess.
"""

import os
import sys
import uvicorn
from pathlib import Path

# Move into src/ so uvicorn resolves 'main:app' and all imports from there
SRC = Path(__file__).parent / "src"
os.chdir(SRC)
sys.path.insert(0, str(SRC))

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )