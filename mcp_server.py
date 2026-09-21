"""Backward-compatible stdio entry point (CAE-Agent-Hub CalculiX convention).

Preferred entry points are the console script ``dualsphysics-mcp`` or
``python -m dualsphysics_mcp``; this shim exists so hosts can also register
``python mcp_server.py`` exactly like the hub's other MCP servers.

Run from the repository root, e.g.:

    .venv/bin/python mcp_server.py
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dualsphysics_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
