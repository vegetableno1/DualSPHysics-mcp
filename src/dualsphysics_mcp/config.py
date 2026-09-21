"""Environment-variable configuration + best-effort discovery of tool paths.

Configuration is env-only (house style; ``.env`` files are the client's
business — see ``.env.example`` at the repo root). Variables:

  DSPH_GENCASE       path to GenCase_linux64
  DSPH_SOLVER        path to the CPU solver (DualSPHysics5.4CPU_linux64, ...)
  DSPH_PARTVTK       path to PartVTK_linux64
  DSPH_MEASURETOOL   path to MeasureTool_linux64
  DSPH_JOBS_DIR      root directory for run_case jobs (default ``./jobs``)
  DSPH_OMP_THREADS   OpenMP threads for the solver (default: all cores)

Unset variables fall back to: (1) ``PATH`` lookup by exact binary name,
(2) a small list of conventional install locations. ``check_environment``
reports which mechanism resolved each tool (source: env | PATH | scan | missing).

Solver runtime libraries: the CPU solver links libChronoEngine.so /
libdsphchrono.so that ship next to the binary. Some installs rely on rpath,
some do not — :func:`solver_runtime_env` therefore always prepends the
solver's directory to ``LD_LIBRARY_PATH`` when spawning jobs.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENV_GENCASE = "DSPH_GENCASE"
ENV_SOLVER = "DSPH_SOLVER"
ENV_PARTVTK = "DSPH_PARTVTK"
ENV_MEASURETOOL = "DSPH_MEASURETOOL"
ENV_JOBS_DIR = "DSPH_JOBS_DIR"
ENV_OMP_THREADS = "DSPH_OMP_THREADS"

TOOL_KEYS = ("gencase", "solver", "partvtk", "measuretool")

#: Conventional install roots scanned for ``<root>/DualSPHysics*/bin/linux``.
SCAN_ROOTS: tuple[Path, ...] = (
    Path("/opt"),
    Path("/usr/local"),
    Path.home() / "softwares",
    Path.home(),
    Path.cwd(),
)

_INSTALL_HINT = (
    "Install DualSPHysics from https://dual.sphysics.org/downloads/ "
    "(browser download) or build the tools from source "
    "(https://github.com/DualSPHysics/DualSPHysics), then set {env} to the "
    "absolute path of the binary."
)

_SOLVER_BUILD_HINT = (
    "The GitHub repository ships prebuilt GenCase/PartVTK/MeasureTool but not "
    "the solver; compile it from the official source with "
    "`make -f Makefile_cpu` (g++ only, no CUDA needed for the CPU build) or "
    "use the full package from https://dual.sphysics.org/downloads/, then set "
    "DSPH_SOLVER to the absolute path of DualSPHysics*CPU_linux64."
)


@dataclass(frozen=True)
class ToolSpec:
    """Static description of one wrapped DualSPHysics tool."""

    key: str
    env_var: str
    role: str
    exact_names: tuple[str, ...]
    glob_patterns: tuple[str, ...] = ()
    build_hint: str = ""


@dataclass(frozen=True)
class ResolvedTool:
    """Result of resolving one tool spec against env / PATH / scan roots."""

    key: str
    env_var: str
    role: str
    path: Path | None
    source: str  # "env" | "PATH" | "scan" | "missing"
    found: bool
    install_hint: str


TOOL_SPECS: dict[str, ToolSpec] = {
    "gencase": ToolSpec(
        key="gencase",
        env_var=ENV_GENCASE,
        role="pre-processor: case XML -> Case.xml + Case.bi4 (particle initial state)",
        exact_names=("GenCase_linux64", "GenCase"),
    ),
    "solver": ToolSpec(
        key="solver",
        env_var=ENV_SOLVER,
        role="SPH solver (CPU, OpenMP): Case.bi4 -> Part_*.bi4 + Run.out",
        exact_names=(),
        glob_patterns=("DualSPHysics*CPU_linux64", "DualSPHysics*CPU*"),
        build_hint=_SOLVER_BUILD_HINT,
    ),
    "partvtk": ToolSpec(
        key="partvtk",
        env_var=ENV_PARTVTK,
        role="post-processor: Part_*.bi4 -> VTK particles files",
        exact_names=("PartVTK_linux64", "PartVTK"),
    ),
    "measuretool": ToolSpec(
        key="measuretool",
        env_var=ENV_MEASURETOOL,
        role="post-processor: SPH interpolation at points -> CSV time series",
        exact_names=("MeasureTool_linux64", "MeasureTool"),
    ),
}


def _candidate_dirs() -> list[Path]:
    """Conventional ``<root>/DualSPHysics*/bin/linux`` directories."""
    dirs: list[Path] = []
    for root in SCAN_ROOTS:
        try:
            matches = sorted(root.glob("DualSPHysics*/bin/linux"))
        except OSError:  # pragma: no cover - unreadable root
            matches = []
        dirs.extend(matches)
        lower = sorted(root.glob("dualsphysics*/bin/linux"))
        dirs.extend(lower)
    return dirs


def _existing(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _scan_for_spec(spec: ToolSpec, scan_roots: list[Path] | None = None) -> Path | None:
    # Explicit scan_roots (even []) REPLACE the conventional roots; None uses them.
    dirs = _candidate_dirs() if scan_roots is None else scan_roots
    best: Path | None = None
    for directory in dirs:
        for name in spec.exact_names:
            candidate = directory / name
            if _existing(candidate):
                return candidate
        for pattern in spec.glob_patterns:
            for candidate in sorted(directory.glob(pattern)):
                if _existing(candidate) and (best is None or candidate.name > best.name):
                    best = candidate
    return best


def resolve_tool(
    key: str,
    env: Mapping[str, str] | None = None,
    scan_roots: list[Path] | None = None,
) -> ResolvedTool:
    """Resolve one tool from env var -> PATH -> scan dirs.

    ``scan_roots`` holds candidate *tool directories* (e.g. ``.../bin/linux``);
    when given (even empty) they REPLACE the conventional scanned roots.
    """
    spec = TOOL_SPECS[key]
    hint = spec.build_hint or _INSTALL_HINT.format(env=spec.env_var)

    env_value = (env or os.environ).get(spec.env_var, "").strip()
    if env_value:
        path = Path(env_value).expanduser()
        return ResolvedTool(
            key=spec.key,
            env_var=spec.env_var,
            role=spec.role,
            path=path,
            source="env",
            found=_existing(path),
            install_hint=hint,
        )

    for name in spec.exact_names:
        which = shutil.which(name)
        if which:
            return ResolvedTool(
                key=spec.key,
                env_var=spec.env_var,
                role=spec.role,
                path=Path(which),
                source="PATH",
                found=True,
                install_hint=hint,
            )

    scanned = _scan_for_spec(spec, scan_roots)
    if scanned is not None:
        return ResolvedTool(
            key=spec.key,
            env_var=spec.env_var,
            role=spec.role,
            path=scanned,
            source="scan",
            found=True,
            install_hint=hint,
        )
    return ResolvedTool(
        key=spec.key,
        env_var=spec.env_var,
        role=spec.role,
        path=None,
        source="missing",
        found=False,
        install_hint=hint,
    )


def resolve_all(
    env: Mapping[str, str] | None = None,
    scan_roots: list[Path] | None = None,
) -> dict[str, ResolvedTool]:
    """Resolve every wrapped tool."""
    return {key: resolve_tool(key, env=env, scan_roots=scan_roots) for key in TOOL_KEYS}


@lru_cache(maxsize=1)
def _cached_resolve(key: str) -> ResolvedTool:
    return resolve_tool(key)


def tool_path(key: str) -> Path | None:
    """Cached path lookup used by the MCP tools (env is read at first call)."""
    return _cached_resolve(key).path


def clear_cache() -> None:
    """Reset the cached resolution (mainly for tests)."""
    _cached_resolve.cache_clear()


def jobs_dir(env: Mapping[str, str] | None = None) -> Path:
    """Jobs root directory (``DSPH_JOBS_DIR`` or ``./jobs``)."""
    value = (env or os.environ).get(ENV_JOBS_DIR, "").strip()
    return Path(value).expanduser() if value else Path.cwd() / "jobs"


def omp_threads(env: Mapping[str, str] | None = None) -> int:
    """OpenMP thread count for the CPU solver (``DSPH_OMP_THREADS`` or cores)."""
    value = (env or os.environ).get(ENV_OMP_THREADS, "").strip()
    if value:
        try:
            threads = int(value)
        except ValueError:
            threads = 0
        if threads >= 1:
            return threads
    return os.cpu_count() or 1


def solver_runtime_env(solver: Path, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for solver subprocesses: prepend its dir to LD_LIBRARY_PATH.

    The solver directory holds libChronoEngine.so / libdsphchrono.so; installs
    without rpath need the variable, installs with rpath are unaffected.
    """
    env = dict(base_env if base_env is not None else os.environ)
    libdir = str(solver.parent)
    existing = env.get("LD_LIBRARY_PATH", "")
    parts = [libdir] + [p for p in existing.split(os.pathsep) if p]
    # de-duplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            ordered.append(part)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(ordered)
    return env
