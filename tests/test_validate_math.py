"""Tests for validate_dambreak math: front extraction, interpolation, metrics."""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import pytest

from dualsphysics_mcp.errors import BadInputError
from dualsphysics_mcp.tools.validate import (
    extract_front_series,
    load_experiment,
    read_measure_csv,
    validate_dambreak,
)

POINTS = [[x, 0.0, 0.03] for x in (1.0, 2.0, 3.0)]


def test_load_experiment_embedded() -> None:
    series = load_experiment("koshizuka1996")
    assert len(series) == 15
    assert series[0] == (0.092031603, 1.128)
    assert series[-1] == (0.751331828, 4.132)
    assert all(t1 > t0 for (t0, _), (t1, _) in pairwise(series))
    with pytest.raises(BadInputError):
        load_experiment("nonexistent")


def test_read_measure_csv_separator_sniffing(tmp_path: Path, sample_measure_csv: Path) -> None:
    rows = read_measure_csv(sample_measure_csv)
    assert [t for t, _ in rows] == [0.10, 0.20, 0.30, 0.40]
    assert rows[0][1] == [1000.0, 0.0, 0.0]

    semicolon = tmp_path / "semi.csv"
    semicolon.write_text("time;r0;r1\n0.1;1000;0\n", encoding="utf-8")
    rows = read_measure_csv(semicolon)
    assert rows == [(0.1, [1000.0, 0.0])]


def test_read_measure_csv_measuretool_layout(tmp_path: Path) -> None:
    """The real MeasureTool -savecsv layout: PosX/Y/Z rows, Part,Time header."""
    csv = tmp_path / "damtip_Rhop.csv"
    csv.write_text(
        " ,PosX [m]:,1,2,3\n"
        " ,PosY [m]:,0,0,0\n"
        " ,PosZ [m]:,0.03,0.03,0.03\n"
        "Part,Time [s],Rhop_0 [kg/m^3],Rhop_1 [kg/m^3],Rhop_2 [kg/m^3]\n"
        "0,0,1000.5,0,0\n"
        "1,0.01,1000.4,1000.6,0\n",
        encoding="utf-8",
    )
    rows = read_measure_csv(csv)
    # (Part, Time) leading columns: time comes from column 2, not column 1.
    assert rows == [(0.0, [1000.5, 0.0, 0.0]), (0.01, [1000.4, 1000.6, 0.0])]


def test_extract_front_series_threshold(sample_measure_csv: Path) -> None:
    rows = read_measure_csv(sample_measure_csv)
    series = extract_front_series(rows, [(p[0], p[1], p[2]) for p in POINTS], 500.0)
    assert series == [(0.10, 1.0), (0.20, 2.0), (0.30, 2.0), (0.40, 3.0)]
    # A threshold nothing reaches yields an empty series.
    assert extract_front_series(rows, [(p[0], p[1], p[2]) for p in POINTS], 2000.0) == []


def test_validate_metrics_hand_computed(tmp_path: Path) -> None:
    """A front grid at 1 mm resolution reproduces the experiment to <= 1 mm."""
    experiment = load_experiment("koshizuka1996")
    points = [[round(i * 0.001, 3), 0.0, 0.03] for i in range(5001)]
    lines = ["time," + ",".join(f"rhop{i}" for i in range(len(points)))]
    for t, x_exp in experiment:
        wet_count = int(x_exp / 0.001) + 1
        values = [1000.0 if i < wet_count else 0.0 for i in range(len(points))]
        lines.append(f"{t:.9f}," + ",".join(f"{v:.1f}" for v in values))
    csv = tmp_path / "measure.csv"
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = validate_dambreak(str(csv), points=points, column_length=1.0)
    assert result["n_samples"] == 15
    # Front resolution is 1 mm; errors must be within one spacing.
    assert result["mae_m"] <= 0.001
    assert result["rmse_m"] <= 0.001
    assert result["max_abs_error_m"] <= 0.001
    assert result["mae_pct_of_column"] <= 0.1
    assert all(s["exp_front_m"] > 0 for s in result["front_series"])


def test_validate_wall_impact_plateau_note(tmp_path: Path) -> None:
    """Front pinned one spacing short of the last point (real wall-impact case)."""
    experiment = load_experiment("koshizuka1996")
    points = [[1.0, 0.0, 0.03], [2.0, 0.0, 0.03], [3.98, 0.0, 0.03], [4.0, 0.0, 0.03]]
    lines = ["Part,Time [s],r0,r1,r2,r3", " ,PosX [m]:,1,2,3.98,4", " ,PosY [m]:,0,0,0,0"]
    for t, x_exp in experiment:
        # front grows to 3.98 and plateaus there; the x=4.0 point never wets
        wet = [1000.0 if (i + 1) <= (2 if x_exp < 2.5 else 3) else 0.0 for i in range(4)]
        lines.append(f"0,{t:.9f}," + ",".join(f"{v:.1f}" for v in wet))
    csv = tmp_path / "plateau.csv"
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = validate_dambreak(str(csv), points=points)
    assert result["notes"] is not None
    assert "plateaued" in result["notes"]
    assert result["impact_time_s"] is not None
    # The front pins at 3.98 from the first sample with x_exp >= 2.5 (index 7).
    assert result["impact_time_s"] == pytest.approx(experiment[7][0], abs=1e-6)


def test_validate_reports_nonzero_errors(tmp_path: Path) -> None:
    experiment = load_experiment("koshizuka1996")
    points = [[1.0, 0.0, 0.03], [2.0, 0.0, 0.03], [3.0, 0.0, 0.03], [4.132, 0.0, 0.03]]
    rows = []
    for t, x_exp in experiment:
        wet = [1000.0 if (i + 1) * 1.0 <= x_exp + 1e-9 else 0.0 for i in range(4)]
        rows.append((t, wet))
    lines = ["time,r0,r1,r2,r3"]
    for t, values in rows:
        lines.append(f"{t:.9f}," + ",".join(f"{v:.1f}" for v in values))
    csv = tmp_path / "coarse.csv"
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = validate_dambreak(str(csv), points=points)
    assert result["n_samples"] == 15
    # Last experimental point (4.132) is beyond the coarse grid except exactly.
    # MAE must equal the mean |sim - exp| of the piecewise-constant front.
    errors = [abs(s["error_m"]) for s in result["front_series"]]
    # mae_m is rounded to 6 decimals in the result payload.
    assert math.isclose(result["mae_m"], sum(errors) / len(errors), abs_tol=1e-6)
    assert result["rmse_m"] >= result["mae_m"]
    assert result["max_abs_error_m"] == max(errors)
    # saturation note fires when the front hits the last point
    assert result["notes"] is not None


def test_validate_column_count_mismatch(tmp_path: Path, sample_measure_csv: Path) -> None:
    with pytest.raises(BadInputError, match="column count mismatch"):
        validate_dambreak(str(sample_measure_csv), points=[[0, 0, 0], [1, 0, 0]])


def test_validate_requires_exactly_one_points_source(sample_measure_csv: Path) -> None:
    with pytest.raises(BadInputError, match="exactly one of points / points_file"):
        validate_dambreak(str(sample_measure_csv))
    with pytest.raises(BadInputError, match="exactly one of points / points_file"):
        validate_dambreak(str(sample_measure_csv), points=POINTS, points_file="whatever.txt")


def test_validate_no_overlap_with_experiment(tmp_path: Path) -> None:
    csv = tmp_path / "late.csv"
    csv.write_text("time,r\n99.0,1000.0\n", encoding="utf-8")
    with pytest.raises(BadInputError, match="do not overlap"):
        validate_dambreak(str(csv), points=[[1.0, 0.0, 0.03]])


def test_validate_max_time_filter(tmp_path: Path, sample_measure_csv: Path) -> None:
    result = validate_dambreak(str(sample_measure_csv), points=POINTS, max_time=0.15)
    # Only t=0.10 survives the filter; but the experiment window starts at 0.092.
    assert result["n_samples"] == 1
    assert result["front_series"][0]["time_s"] == 0.10
