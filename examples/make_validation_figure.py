#!/usr/bin/env python3
"""Validation figure: simulated dam-break front vs Koshizuka & Oka (1996).

Reads the MeasureTool dam-tip CSV produced by ``measure_tool`` (with the same
points file that produced it), reconstructs the surge-front position with the
same wetness-threshold logic as the ``validate_dambreak`` MCP tool, and draws
the money plot for the README / demos:

* experiment (digitised 1996 series) as markers,
* simulation as a line,
* the error band between the two curves,
* a marker at the wall-impact time (front plateau at the tank end),
* a text box with the headline numbers (n, MAE, RMSE, max error, MAE % of
  the 1 m column).

Typical use after the demo run::

    python examples/make_validation_figure.py \\
        --csv <job_dir>/measure/damtip_Rhop.csv \\
        --out docs/img/validation_dambreak.png

Dev dependency only: matplotlib (``uv add --dev matplotlib``); the parsing /
metrics reuse the pure-Python ``dualsphysics_mcp.tools.validate`` module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dualsphysics_mcp.errors import DualSphysError
from dualsphysics_mcp.tools import validate

DEFAULT_POINTS = Path(__file__).resolve().parent / "dambreak_val2d" / "points_damtip.txt"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "docs" / "img" / "validation_dambreak.png"

_SIM_LABEL = "DualSPHysics (this MCP run)"
_EXP_LABEL = "Koshizuka & Oka (1996) experiment"


class FigureError(RuntimeError):
    """User-facing failure (missing dependency, unreadable inputs)."""


def format_metrics_box(result: dict[str, Any], column_length: float) -> str:
    """One-box summary of the validation numbers (pure; easy to test)."""
    lines = [
        f"n = {result['n_samples']} samples",
        f"MAE = {result['mae_m']:.3f} m"
        f"  ({result['mae_pct_of_column']:.1f}% of a={column_length:g} m)",
        f"RMSE = {result['rmse_m']:.3f} m",
        f"max |err| = {result['max_abs_error_m']:.3f} m",
    ]
    if result.get("impact_time_s") is not None:
        lines.append(f"wall impact at t = {result['impact_time_s']:.2f} s")
    return "\n".join(lines)


def make_figure(
    csv_path: Path,
    points_file: Path,
    out: Path,
    threshold: float = 500.0,
    column_length: float = 1.0,
) -> Path:
    """Compute the comparison and write the PNG; returns the output path."""
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatched sys.modules
        raise FigureError(
            "matplotlib is required for the validation figure; install it with: "
            "uv add --dev matplotlib   (or: pip install matplotlib)"
        ) from exc
    matplotlib.use("Agg")  # headless-safe
    import matplotlib.pyplot as plt

    result = validate.validate_dambreak(
        csv_path=str(csv_path),
        points_file=str(points_file),
        threshold=threshold,
        column_length=column_length,
    )
    sim_t = [s["time_s"] for s in result["front_series"]]
    sim_x = [s["sim_front_m"] for s in result["front_series"]]
    exp_t = [t for t, _ in validate.load_experiment("koshizuka1996")]
    exp_x = [x for _, x in validate.load_experiment("koshizuka1996")]
    exp_on_sim = [s["exp_front_m"] for s in result["front_series"]]

    fig, ax = plt.subplots(figsize=(9.6, 5.6), dpi=150)
    ax.fill_between(
        sim_t, sim_x, exp_on_sim, color="tab:blue", alpha=0.18, label="error band (sim - exp)"
    )
    ax.plot(exp_t, exp_x, "ko", ms=5, mfc="none", mew=1.2, label=_EXP_LABEL)
    ax.plot(sim_t, sim_x, "-", color="tab:blue", lw=1.8, label=_SIM_LABEL)

    impact = result.get("impact_time_s")
    if impact is not None:
        ax.axvline(impact, color="tab:red", ls="--", lw=1.0, alpha=0.8)
        ax.annotate(
            f"wall impact\nt = {impact:.2f} s",
            xy=(impact, max(sim_x) * 0.30),
            xytext=(impact - 0.28, max(sim_x) * 0.42),
            color="tab:red",
            fontsize=9,
            arrowprops={"arrowstyle": "->", "color": "tab:red", "lw": 0.9},
        )
    ax.text(
        0.97,
        0.05,
        format_metrics_box(result, column_length),
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=8.5,
        family="monospace",
        bbox={"boxstyle": "round,pad=0.45", "facecolor": "white", "edgecolor": "0.6", "alpha": 0.9},
    )
    ax.set_xlabel("time (s)")
    ax.set_ylabel("surge-front position x (m)")
    ax.set_title(
        "2D dam-break validation — SPH simulation vs Koshizuka & Oka (1996) experiment",
        fontsize=11,
    )
    ax.grid(alpha=0.25, lw=0.5)
    ax.legend(loc="upper left", fontsize=9)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_validation_figure.py",
        description="Plot the measure_tool dam-tip CSV against Koshizuka & Oka (1996).",
    )
    parser.add_argument(
        "--csv", type=Path, required=True, help="MeasureTool time-history CSV (damtip points)"
    )
    parser.add_argument(
        "--points",
        type=Path,
        default=DEFAULT_POINTS,
        help="points file used for the measurement (columns map to points by order)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output PNG path")
    parser.add_argument(
        "--threshold",
        type=float,
        default=500.0,
        help="wetness threshold on rhop (kg/m^3) for the front extraction",
    )
    parser.add_argument(
        "--column-length", type=float, default=1.0, help="water-column length a (m)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        make_figure(
            csv_path=args.csv,
            points_file=args.points,
            out=args.out,
            threshold=args.threshold,
            column_length=args.column_length,
        )
    except (FigureError, DualSphysError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
