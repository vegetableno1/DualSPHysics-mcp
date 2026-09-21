"""check_environment: probe tool paths, versions and solver features."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .. import config

_VERSION_RE = re.compile(r"v(\d+(?:\.\d+)+[^\s,]*)")
_PROBE_TIMEOUT_S = 20.0


def _run_probe(path: Path, arg: str) -> tuple[int | None, str]:
    """Run ``<tool> <arg>`` and capture combined output; (None, error) on crash."""
    try:
        proc = subprocess.run(
            [str(path), arg],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            env=config.solver_runtime_env(path),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def probe_version(path: Path) -> str | None:
    """Extract e.g. '5.4.354.01' from the tool's ``-ver`` banner.

    Some tools print the banner and still exit non-zero (GenCase -ver exits 1),
    so the banner itself is the evidence, not the exit code.
    """
    _, output = _run_probe(path, "-ver")
    match = _VERSION_RE.search(output)
    return match.group(1) if match else None


def probe_solver_features(path: Path) -> dict[str, Any] | None:
    """Parse the solver's ``-info`` JSON (features such as CPU/GPU/WaveGen)."""
    _, output = _run_probe(path, "-info")
    start = output.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(output[start:])
    except json.JSONDecodeError:
        return None
    features = payload.get("Features")
    return features if isinstance(features, dict) else None


def check_environment() -> dict[str, Any]:
    """Report every wrapped tool: path, provenance, version (+ solver features)."""
    tools: list[dict[str, Any]] = []
    for key in config.TOOL_KEYS:
        resolved = config.resolve_tool(key)
        entry: dict[str, Any] = {
            "name": key,
            "role": resolved.role,
            "env_var": resolved.env_var,
            "path": str(resolved.path) if resolved.path else None,
            "found": resolved.found,
            "source": resolved.source,
        }
        if resolved.found and resolved.path is not None:
            entry["version"] = probe_version(resolved.path)
            if key == "solver":
                features = probe_solver_features(resolved.path)
                if features is not None:
                    entry["features"] = features
                    if features.get("WaveGen") is False:
                        entry["notes"] = (
                            "solver built without wave generation (-DDISABLE_WAVEGEN): "
                            "wave / wavemaker cases cannot run; dam-break and other "
                            "gravity-driven cases are unaffected"
                        )
        else:
            entry["version"] = None
            entry["install_hint"] = resolved.install_hint
        tools.append(entry)

    threads = config.omp_threads()
    jobs = config.jobs_dir()
    return {
        "tools": tools,
        "omp_threads": threads,
        "jobs_dir": str(jobs),
        "jobs_dir_exists": jobs.is_dir(),
        "ready": all(bool(entry["found"]) for entry in tools),
        "missing": [str(entry["name"]) for entry in tools if not entry["found"]],
        "hint": (
            "Set DSPH_GENCASE / DSPH_SOLVER / DSPH_PARTVTK / DSPH_MEASURETOOL to the "
            "absolute paths of your DualSPHysics binaries (see .env.example)."
        ),
    }
