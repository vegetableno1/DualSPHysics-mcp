"""Shared fixtures: sample Run.out logs, synthetic CSVs, toolchain probes."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_toolchain() -> Path:
    """Machine-neutral probe: env override -> discovered gencase dir -> dummy."""
    override = os.environ.get("DSPH_TEST_TOOLCHAIN")
    if override:
        return Path(override)
    from dualsphysics_mcp import config

    gencase = config.resolve_tool("gencase").path
    return gencase.parent if gencase is not None else Path("/nonexistent-dualsphysics")


#: Local DualSPHysics install used for the (skip-if-absent) integration tests.
#: Override with DSPH_TEST_TOOLCHAIN to prove the suite is green without one.
LOCAL_TOOLCHAIN = _default_toolchain()

#: Real v5.0-format rows (CaseDambreakVal2D run, DualSPHysics v5.0.140).
RUNOUT_V50 = """\
DualSPHysics5 v5.0.140 (18-07-2020)
====================================
Threads by host for parallel execution: 8
XML-Vars (parameters): TimeMax=[2]  TimeOut=[0.01]
**Basic case configuration is loaded
Loading initial state of particles...
Loaded particles: 21001
CaseNp=21001
CaseNbound=1001
CaseNfluid=20000
TimeMax=2
TimePart=0.01
Particle summary:
  Fixed....: 1001  id:(0-1000)   MKs:1 (10)
  Fluid....: 20000  id:(1001-21000)   MKs:1 (1)

Total particles: 21001 (bound=1001 (fx=1001 mv=0 ft=0) fluid=20000)

Part_0000        21001 particles successfully stored

[Initialising simulation (uni99t3v  21-06-2022 22:31:45)]
PART       PartTime      TotalSteps    Steps    Time/Sec   Finish time
=========  ============  ============  =======  =========  ===================
Part_0001      0.010016           314      314     494.50  21-06-2022 22:48:14
Part_0002      0.020012           628      314     200.29  21-06-2022 22:25:34
Part_0003      0.030018           942      314     215.23  21-06-2022 22:25:38
"""

#: Real v5.4-format rows (CaseDambreakVal2D run, locally built 5.4.355 CPU).
#: Note the thousands separators and the plain part number (%05d, no Part_).
RUNOUT_V54 = """\
DualSPHysics5 v5.4.355 (08-04-2025)
====================================
Threads by host for parallel execution: 8
Loaded particles: 21,001
CaseNp=21,001
CaseNbound=1,001
CaseNfluid=20,000
TimeMax=2
TimePart=0.01
RunMode="Pos-Double - OpenMP(Threads:8)"

Particle summary:
  Fixed....: 1,001  id:(0-1000)   MKs:1 (10)
  Fluid....: 20,000  id:(1001-21000)   MKs:1 (1)

Total particles: 21,001 (bound=1001 (fx=1001 mv=0 ft=0) fluid=20000)

Part_0000        21,001 (100.0%) particles successfully stored

[Initialising simulation (k71zjhq2)  21-09-2026 00:36:51)]
PART   PartTime   TotalSteps   Steps    Particles    Cells        Time/Sec   Finish time
=====  =========  ===========  =======  ===========  ===========  =========  ===================
00001     0.010017           314      314       21,001        2,736     216.96  21-09-2026 00:37:07
00002     0.020012           628      314       21,001        2,736     200.29  21-09-2026 00:37:10
00003     0.030025           942      314       21,001        2,736     205.28  21-09-2026 00:37:15
"""

RUNOUT_COMPLETED = (
    RUNOUT_V54
    + """\
00200     2.000031       63,001      320       21,001        5,120     640.11  21-09-2026 00:51:02

Finished execution (code=0).
"""
)

RUNOUT_FAILED = (
    RUNOUT_V54
    + """\
*** Exception(exc): ErrReadBi4: the file does not exist (...)

Finished execution (code=1).
"""
)


def local_tool_exists(name: str) -> bool:
    candidate = LOCAL_TOOLCHAIN / name
    return candidate.is_file() and os.access(candidate, os.X_OK)


def gencase_available() -> bool:
    return local_tool_exists("GenCase_linux64")


def solver_available() -> bool:
    return bool(LOCAL_TOOLCHAIN.glob("DualSPHysics*CPU_linux64")) and any(
        local_tool_exists(p.name) for p in LOCAL_TOOLCHAIN.glob("DualSPHysics*CPU_linux64")
    )


def write_fake_binary(path: Path, payload: str = "#!/bin/sh\necho fake\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def fake_toolchain(tmp_path: Path) -> Path:
    """A fake DualSPHysics/bin/linux tree with runnable stub binaries."""
    root = tmp_path / "DualSPHysics_fake" / "bin" / "linux"
    write_fake_binary(root / "GenCase_linux64")
    write_fake_binary(root / "DualSPHysics5.4CPU_linux64")
    write_fake_binary(root / "PartVTK_linux64")
    write_fake_binary(root / "MeasureTool_linux64")
    return root


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Env with DSPH_* cleared and a minimal PATH (no accidental discovery)."""
    for key in (
        "DSPH_GENCASE",
        "DSPH_SOLVER",
        "DSPH_PARTVTK",
        "DSPH_MEASURETOOL",
        "DSPH_JOBS_DIR",
        "DSPH_OMP_THREADS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return {"PATH": "/usr/bin:/bin"}


@pytest.fixture()
def sample_measure_csv(tmp_path: Path) -> Path:
    """Synthetic MeasureTool CSV: 3 points at x=1,2,3, comma separated.

    Front progression: wet up to x=1 at t=0.1, x=2 at t=0.2-0.3, x=3 later.
    """
    lines = ["time,rhop(1,0,0.03),rhop(2,0,0.03),rhop(3,0,0.03)"]
    for t, values in [
        (0.10, (1000.0, 0.0, 0.0)),
        (0.20, (1000.0, 1000.0, 0.0)),
        (0.30, (1000.0, 1000.0, 0.0)),
        (0.40, (1000.0, 1000.0, 1000.0)),
    ]:
        lines.append(f"{t:.2f}," + ",".join(f"{v:.1f}" for v in values))
    path = tmp_path / "measure.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "solver: needs the local DualSPHysics toolchain (skipped otherwise)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if shutil.which("bash") is None:  # pragma: no cover - sanity guard
        return
    skip = pytest.mark.skip(reason="local DualSPHysics toolchain not available")
    for item in items:
        if "solver" in item.keywords and not (gencase_available() and solver_available()):
            item.add_marker(skip)
