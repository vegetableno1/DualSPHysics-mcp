"""Tests for partvtk / measure_tool command construction and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from dualsphysics_mcp.errors import BadInputError
from dualsphysics_mcp.tools import postprocess
from dualsphysics_mcp.tools.validate import parse_points_file
from tests.conftest import write_fake_binary


def test_build_partvtk_command() -> None:
    exe = Path("/tools/PartVTK_linux64")
    argv = postprocess.build_partvtk_command(
        exe,
        Path("/job/data"),
        "/job/particles/PartFluid",
        onlytype="-all,fluid",
        variables="+idp,+vel,+rhop",
        first=0,
        last=10,
    )
    assert argv == [
        str(exe),
        "-dirdata",
        "/job/data",
        "-savevtk",
        "/job/particles/PartFluid",
        "-onlytype:-all,fluid",
        "-vars:+idp,+vel,+rhop",
        "-first:0",
        "-last:10",
    ]


def test_build_measure_command_points_file_and_csvsep() -> None:
    exe = Path("/tools/MeasureTool_linux64")
    argv = postprocess.build_measure_command(
        exe,
        Path("/job/data"),
        "/job/measure/damtip",
        points_file="/examples/points_damtip.txt",
    )
    assert argv[:6] == [
        str(exe),
        "-dirdata",
        "/job/data",
        "-points",
        "/examples/points_damtip.txt",
        "-onlytype:-all,+fluid",
    ]
    assert "-vars:-all,rhop" in argv
    assert argv[argv.index("-savecsv") + 1] == "/job/measure/damtip"
    assert argv[-1] == "-csvsep:1"  # deterministic comma separator


def test_build_measure_command_pointsdef_and_csvsep_override() -> None:
    exe = Path("/tools/MeasureTool_linux64")
    argv = postprocess.build_measure_command(
        exe,
        Path("/job/data"),
        "/job/measure/x",
        pointsdef="ptels[x=0:0.01:4,z=0.03]",
        extra_args=["-csvsep:0"],
    )
    assert "-pointsdef:ptels[x=0:0.01:4,z=0.03]" in argv
    assert argv[-1] == "-csvsep:0"  # caller's separator wins
    assert argv.count("-csvsep:1") == 0


def test_build_measure_command_requires_exactly_one_points_source() -> None:
    with pytest.raises(BadInputError, match="exactly one of points_file / pointsdef"):
        postprocess.build_measure_command(Path("/m"), Path("/d"), "/o")
    with pytest.raises(BadInputError, match="exactly one of points_file / pointsdef"):
        postprocess.build_measure_command(
            Path("/m"), Path("/d"), "/o", points_file="a", pointsdef="b"
        )


def test_bad_input_wins_over_tool_missing(clean_env, monkeypatch) -> None:
    """Argument violations raise DSPH_BAD_INPUT even with NO toolchain at all.

    Regression guard for the CI failure: discovery used to run first, so on a
    machine without DualSPHysics (GitHub Actions) these raised ToolMissingError
    while local runs passed via the ~/softwares scan. Pure parameter rules
    must never depend on tool existence (spec error matrix: BAD_INPUT first).
    """
    from dualsphysics_mcp import config

    monkeypatch.setattr(config, "_candidate_dirs", lambda: [])
    monkeypatch.setattr(config, "SCAN_ROOTS", ())
    config.clear_cache()
    try:
        with pytest.raises(BadInputError, match="exactly one of dirdata / job_id"):
            postprocess.run_partvtk(dirdata="/x", job_id="job-1")
        with pytest.raises(BadInputError, match="exactly one of dirdata / job_id"):
            postprocess.run_partvtk()
        with pytest.raises(BadInputError, match="exactly one of points_file / pointsdef"):
            postprocess.run_measure_tool(dirdata="/x", job_id="job-1")
    finally:
        config.clear_cache()


def test_resolve_dirdata_job_layout(tmp_path: Path) -> None:
    data_dir = tmp_path / "jobs" / "job-x" / "data"
    data_dir.mkdir(parents=True)
    resolved = postprocess._resolve_dirdata(None, "job-x", jobs_root=tmp_path / "jobs")
    assert resolved == data_dir
    with pytest.raises(BadInputError, match="particle data directory not found"):
        postprocess._resolve_dirdata(None, "job-missing", jobs_root=tmp_path / "jobs")


def test_run_partvtk_missing_tool(clean_env, monkeypatch, tmp_path: Path) -> None:
    from dualsphysics_mcp import config

    monkeypatch.setattr(config, "_candidate_dirs", lambda: [])
    monkeypatch.setattr(config, "SCAN_ROOTS", ())
    data = tmp_path / "data"
    data.mkdir()
    config.clear_cache()
    try:
        with pytest.raises(Exception, match="DSPH_TOOL_MISSING"):
            postprocess.run_partvtk(dirdata=str(data))
    finally:
        config.clear_cache()


def test_run_measure_tool_end_to_end_stub(
    fake_toolchain: Path, monkeypatch, tmp_path: Path
) -> None:
    """A stub MeasureTool that echoes and creates the expected CSV file."""
    stub = fake_toolchain / "MeasureTool_linux64"
    write_fake_binary(
        stub,
        "#!/bin/sh\n"
        'out=""; next=0\n'
        'for a in "$@"; do\n'
        '  if [ "$next" = 1 ]; then out="$a"; next=0; fi\n'
        '  if [ "$a" = "-savecsv" ]; then next=1; fi\n'
        "done\n"
        'printf "time;rhop\\n0.1;1000\\n" > "${out}_damtip.csv"\n'
        "exit 0\n",
    )
    monkeypatch.setenv("DSPH_MEASURETOOL", str(stub))
    from dualsphysics_mcp import config

    config.clear_cache()
    try:
        data = tmp_path / "job" / "data"
        data.mkdir(parents=True)
        points = tmp_path / "points.txt"
        points.write_text('"POINTS"\n0 0 0.03\n0.01 0 0.03\n', encoding="utf-8")
        result = postprocess.run_measure_tool(
            dirdata=str(data),
            points_file=str(points),
            savecsv=str(tmp_path / "measure" / "damtip"),
            jobs_root=tmp_path,
        )
    finally:
        config.clear_cache()
    assert result["returncode"] == 0
    assert result["csv_files"] and result["csv_files"][0].endswith("_damtip.csv")
    assert "-csvsep:1" in result["command"]


def test_parse_points_file_all_dialects(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.txt"
    explicit.write_text('"POINTS"\n0 0 0.03\n1 0 0.03\n', encoding="utf-8")
    assert parse_points_file(explicit) == [(0.0, 0.0, 0.03), (1.0, 0.0, 0.03)]

    count_list = tmp_path / "count.txt"
    count_list.write_text('"POINTSLIST"\n0 0 0.03\n0.5 0 0\n3 1 1\n', encoding="utf-8")
    assert parse_points_file(count_list) == [
        (0.0, 0.0, 0.03),
        (0.5, 0.0, 0.03),
        (1.0, 0.0, 0.03),
    ]

    end_list = tmp_path / "end.txt"
    end_list.write_text('"POINTSENDLIST"\n0 0 0.03\n0.5 0 0\n1 0 0\n', encoding="utf-8")
    assert parse_points_file(end_list) == [
        (0.0, 0.0, 0.03),
        (0.5, 0.0, 0.03),
        (1.0, 0.0, 0.03),
    ]

    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(BadInputError):
        parse_points_file(empty)
