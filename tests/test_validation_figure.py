"""Lightweight tests for examples/make_validation_figure.py.

The end-to-end figure test needs only matplotlib (dev dependency, Agg
backend — no display); everything else is pure parsing/formatting.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_example(name: str) -> ModuleType:
    path = REPO_ROOT / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


figscript = _load_example("make_validation_figure")

_RESULT = {
    "n_samples": 66,
    "mae_m": 0.2196,
    "rmse_m": 0.2401,
    "max_abs_error_m": 0.3249,
    "mae_pct_of_column": 21.96,
    "impact_time_s": 0.67,
}


def _synthetic_points(tmp_path: Path) -> Path:
    """MeasureTool points file: x = 0..4 step 0.1 at z=0.03 (41 points)."""
    points = tmp_path / "points.txt"
    points.write_text("POINTSENDLIST\n0.0 0.0 0.03\n0.1 0.0 0.0\n4.0 0.0 0.0\n", encoding="utf-8")
    return points


def _synthetic_csv(tmp_path: Path) -> Path:
    """MeasureTool-layout CSV: front x = min(1+3t, 3.4) wet, 0 dry beyond."""
    xs = [round(i * 0.1, 2) for i in range(41)]
    lines = ["Part,Time [s]," + ",".join(f"Rhop_{i}" for i in range(41))]
    for step, n in enumerate(range(20)):
        t = round(n * 0.05, 3)
        front = min(1.0 + 3.0 * t, 3.4)
        cells = ["1000.0" if x <= front + 1e-9 else "0.0" for x in xs]
        lines.append(f"{step:04d},{t:.2f}," + ",".join(cells))
    csv = tmp_path / "damtip_Rhop.csv"
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv


class TestMetricsBox:
    def test_contains_headline_numbers(self) -> None:
        text = figscript.format_metrics_box(_RESULT, column_length=1.0)
        assert "n = 66" in text
        assert "MAE = 0.220 m  (22.0% of a=1 m)" in text
        assert "RMSE = 0.240 m" in text
        assert "max |err| = 0.325 m" in text
        assert "wall impact at t = 0.67 s" in text

    def test_impact_line_omitted_when_absent(self) -> None:
        result = dict(_RESULT, impact_time_s=None)
        assert "wall impact" not in figscript.format_metrics_box(result, column_length=1.0)


class TestFigure:
    def test_figure_written_from_synthetic_csv(self, tmp_path: Path) -> None:
        pytest.importorskip("matplotlib")
        out = tmp_path / "fig.png"
        path = figscript.make_figure(
            csv_path=_synthetic_csv(tmp_path),
            points_file=_synthetic_points(tmp_path),
            out=out,
        )
        assert path == out
        assert out.is_file() and out.stat().st_size > 10_000

    def test_main_returns_2_for_missing_csv(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            figscript.main(
                [
                    "--csv",
                    str(tmp_path / "nonexistent.csv"),
                    "--points",
                    str(_synthetic_points(tmp_path)),
                    "--out",
                    str(tmp_path / "never.png"),
                ]
            )
            == 2
        )
        assert "not found" in capsys.readouterr().err


class TestCli:
    def test_defaults(self) -> None:
        args = figscript.build_parser().parse_args(["--csv", "measure/damtip_Rhop.csv"])
        assert args.points.name == "points_damtip.txt"
        assert args.out.name == "validation_dambreak.png"
        assert (args.threshold, args.column_length) == (500.0, 1.0)
