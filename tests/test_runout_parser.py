"""Tests for the Run.out progress parser (both row formats + end states)."""

from __future__ import annotations

from dualsphysics_mcp.tools.runout import parse_run_out, parse_run_out_file
from tests.conftest import RUNOUT_COMPLETED, RUNOUT_FAILED, RUNOUT_V50, RUNOUT_V54


def test_v50_rows() -> None:
    progress = parse_run_out(RUNOUT_V50)
    assert progress.tmax == 2.0
    assert progress.part == 3
    assert progress.part_time == 0.030018
    assert progress.total_steps == 942
    assert progress.steps_last_part == 314
    assert progress.wall_sec_per_sim_sec == 215.23
    assert progress.solver_eta == "21-06-2022 22:25:38"
    assert progress.percent == 1.5  # 0.030018 / 2
    assert progress.particles is None and progress.cells is None  # v5.0: no such columns
    assert progress.loaded_particles == 21001
    assert progress.case_np == 21001
    assert progress.case_nfluid == 20000
    assert progress.case_nbound == 1001
    assert progress.finished_code is None
    assert progress.exception_text is None


def test_v54_rows_with_thousands_separators() -> None:
    progress = parse_run_out(RUNOUT_V54)
    assert progress.tmax == 2.0
    assert progress.part == 3
    assert progress.part_time == 0.030025
    assert progress.total_steps == 942
    assert progress.particles == 21001  # "21,001"
    assert progress.cells == 2736  # "2,736"
    assert progress.wall_sec_per_sim_sec == 205.28
    assert progress.solver_eta == "21-09-2026 00:37:15"
    assert progress.percent == 1.5
    assert progress.case_np == 21001
    assert progress.case_nbound == 1001
    assert progress.case_nfluid == 20000


def test_completed_run() -> None:
    progress = parse_run_out(RUNOUT_COMPLETED)
    assert progress.finished_code == 0
    assert progress.part == 200
    assert progress.part_time == 2.000031
    assert progress.percent == 100.0
    assert progress.total_steps == 63001


def test_failed_run() -> None:
    progress = parse_run_out(RUNOUT_FAILED)
    assert progress.finished_code == 1
    assert progress.exception_text is not None
    assert "Exception" in progress.exception_text


def test_part0_storage_line_is_not_a_progress_row() -> None:
    progress = parse_run_out(RUNOUT_V54)
    # Part_0000 storage line must not override real rows (part 1-3 exist).
    assert progress.part == 3
    assert progress.percent != 0.0


def test_empty_and_partial_logs() -> None:
    empty = parse_run_out("")
    assert empty.tmax is None
    assert empty.part is None
    assert empty.percent is None
    assert empty.finished_code is None

    startup = parse_run_out("TimeMax=2\nLoading initial state of particles...\n")
    assert startup.tmax == 2.0
    assert startup.part is None


def test_tmax_from_xml_vars_bracket_form() -> None:
    progress = parse_run_out("XML-Vars (parameters): TimeMax=[2]  TimeOut=[0.01]\n")
    assert progress.tmax == 2.0


def test_parse_run_out_file_missing(tmp_path) -> None:
    progress = parse_run_out_file(tmp_path / "does_not_exist.out")
    assert progress.part is None
    assert progress.finished_code is None
