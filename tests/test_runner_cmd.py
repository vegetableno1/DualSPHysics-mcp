"""Tests for solver job command construction (run_case argv building)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dualsphysics_mcp.errors import BadInputError
from dualsphysics_mcp.tools import runner


def test_build_solver_command_basic() -> None:
    exe = Path("/tools/DualSPHysics5.4CPU_linux64")
    argv = runner.build_solver_command(
        exe, Path("/jobs/out/CaseX.xml"), Path("/jobs/job-1"), omp_threads=4
    )
    assert argv == [
        str(exe),
        "/jobs/out/CaseX",  # .xml stripped for the solver's case base
        "/jobs/job-1",
        "-cpu",
        "-ompthreads:4",
    ]


def test_build_solver_command_flags() -> None:
    exe = Path("/tools/solver")
    argv = runner.build_solver_command(
        exe,
        Path("/out/CaseX"),
        Path("/jobs/job-1"),
        tmax=1.5,
        tout=0.02,
        extra_args=["-svres"],
    )
    assert argv[1] == "/out/CaseX"
    assert "-tmax:1.5" in argv and "-tout:0.02" in argv and "-svres" in argv
    # no explicit threads -> the config default is applied at start time
    assert any(arg.startswith("-ompthreads:") for arg in argv)


def test_build_solver_command_dotted_case_name() -> None:
    """``Run2026.1(.xml)`` keeps its dots (Path.with_suffix would eat ``.1``)."""
    exe = Path("/tools/solver")
    argv = runner.build_solver_command(exe, Path("/out/Run2026.1.xml"), Path("/j"))
    assert argv[1] == "/out/Run2026.1"
    argv = runner.build_solver_command(exe, Path("/out/Run2026.1"), Path("/j"))
    assert argv[1] == "/out/Run2026.1"


def test_job_id_validation_rejects_traversal() -> None:
    with pytest.raises(BadInputError, match="invalid job id"):
        runner._job_dir("../escape")
    with pytest.raises(BadInputError, match="invalid job id"):
        runner._job_dir("job/with/slash")
