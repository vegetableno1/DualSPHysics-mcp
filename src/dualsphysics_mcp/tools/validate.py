"""validate_dambreak: simulated dam-tip front vs Koshizuka & Oka (1996).

Pure Python (no solver, no numpy): reads the CSV produced by ``measure_tool``
with a horizontal line of points at fixed z and a single interpolated variable
(typically ``-vars:-all,rhop``), reconstructs the surge-front position per
output time as the furthest point whose value exceeds a wetness threshold
(e.g. 500 kg/m^3 for density; points beyond the front receive the kernel
dummy value 0), interpolates the embedded experimental series at the same
instants and reports per-time errors plus aggregate metrics.

Column mapping is BY POINT ORDER: column ``i`` after the time column belongs
to the ``i``-th point of the points definition, whatever the CSV header names
look like — so pass the SAME points file/definition to validate_dambreak that
produced the CSV.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from ..errors import BadInputError

EXPERIMENTS = ("koshizuka1996",)

_WET_THRESHOLD_DEFAULT = 500.0


def load_experiment(name: str = "koshizuka1996") -> list[tuple[float, float]]:
    """Embedded (time_s, x_m) pairs of the experimental dam-tip series."""
    if name not in EXPERIMENTS:
        raise BadInputError(f"unknown experiment {name!r}; available: {', '.join(EXPERIMENTS)}")
    resource = resources.files("dualsphysics_mcp").joinpath("data/koshizuka_oka_1996_damtip.csv")
    text = resource.read_text(encoding="utf-8")
    series: list[tuple[float, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 2 and parts[0][0].isdigit():
            series.append((float(parts[0]), float(parts[1])))
    if not series:
        raise BadInputError("embedded experiment data is empty")
    return series


def parse_points_file(path: Path) -> list[tuple[float, float, float]]:
    """Parse MeasureTool points files: POINTS / POINTSLIST / POINTSENDLIST."""
    text = path.read_text(encoding="utf-8", errors="replace")
    points: list[tuple[float, float, float]] = []
    mode: str | None = None
    buffer: list[list[float]] = []

    def flush() -> None:
        nonlocal buffer
        if mode == "POINTS":
            for nums in buffer:
                if len(nums) >= 3:
                    points.append((nums[0], nums[1], nums[2]))
        elif mode == "POINTSLIST" and len(buffer) == 3 and all(len(r) >= 3 for r in buffer):
            bx, by, bz = buffer[0][:3]
            dx, dy, dz = buffer[1][:3]
            for i in range(int(buffer[2][0])):
                for j in range(int(buffer[2][1])):
                    for k in range(int(buffer[2][2])):
                        points.append((bx + i * dx, by + j * dy, bz + k * dz))
        elif mode == "POINTSENDLIST" and len(buffer) == 3 and all(len(r) >= 3 for r in buffer):
            bx, by, bz = buffer[0][:3]
            dx, dy, dz = buffer[1][:3]
            ex, ey, ez = buffer[2][:3]
            nx = round((ex - bx) / dx) if dx else 0
            ny = round((ey - by) / dy) if dy else 0
            nz = round((ez - bz) / dz) if dz else 0
            for i in range(nx + 1):
                for j in range(ny + 1):
                    for k in range(nz + 1):
                        points.append((bx + i * dx, by + j * dy, bz + k * dz))
        buffer = []

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        upper = line.upper().strip('"')
        if upper in ("POINTS", "POINTSLIST", "POINTSENDLIST"):
            flush()
            mode = upper
            continue
        nums: list[float] = []
        for token in line.replace(",", " ").split():
            try:
                nums.append(float(token))
            except ValueError:
                nums = []
                break
        if nums:
            buffer.append(nums)
    flush()
    if not points:
        raise BadInputError(f"no points parsed from {path}")
    return points


def read_measure_csv(path: Path) -> list[tuple[float, list[float]]]:
    """Read a MeasureTool time-history CSV (';' or ',' separated, header-safe).

    Real layout (verified against MeasureTool v5.4 ``-savecsv`` with points):

    ================ ========================================================
    row 1-3          ``<empty>,PosX [m]:,...`` position header rows
    row 4            ``Part,Time [s],Rhop_0 [...],Rhop_1 [...]`` var header
    row 5+           ``<part>,<time>,<v0>,<v1>,...`` data rows
    ================ ========================================================

    So in MeasureTool layout the data rows carry TWO leading columns (Part,
    Time). Files without the ``Part,Time`` header are read generically with
    the first column as time (one value column per point).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return []
    first = lines[0]
    separator = ";" if first.count(";") >= first.count(",") else ","
    # The var-header row ("Part,Time [s],Rhop_0...") may sit behind the
    # position-header rows (PosX/PosY/PosZ), so scan for it anywhere.
    measuretool_layout = any(
        (cells := line.split(separator))[:1] == ["Part"]
        and len(cells) > 1
        and cells[1].startswith("Time")
        for line in lines
    )
    rows: list[tuple[float, list[float]]] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cells = line.split(separator)
        try:
            if measuretool_layout:
                time_value = float(cells[1])
                values = [float(cell) for cell in cells[2:]]
            else:
                time_value = float(cells[0])
                values = [float(cell) for cell in cells[1:]]
        except (ValueError, IndexError):
            continue  # header or decoration line
        rows.append((time_value, values))
    return rows


def extract_front_series(
    rows: list[tuple[float, list[float]]],
    points: list[tuple[float, float, float]],
    threshold: float,
) -> list[tuple[float, float]]:
    """(time, front_x) per row: the furthest wet point along the line."""
    series: list[tuple[float, float]] = []
    for time_value, values in rows:
        wet = [points[i][0] for i, value in enumerate(values) if value >= threshold]
        if wet:
            series.append((time_value, max(wet)))
    return series


def _interp(series: list[tuple[float, float]], t: float) -> float | None:
    """Linear interpolation; None outside the series range."""
    if t < series[0][0] or t > series[-1][0]:
        return None
    for index in range(1, len(series)):
        (t0, v0), (t1, v1) = series[index - 1], series[index]
        if t0 <= t <= t1:
            if t1 == t0:
                return v0
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return None


def validate_dambreak(
    csv_path: str,
    points: list[list[float]] | None = None,
    points_file: str | None = None,
    threshold: float = _WET_THRESHOLD_DEFAULT,
    column_length: float = 1.0,
    experiment: str = "koshizuka1996",
    max_time: float | None = None,
) -> dict[str, Any]:
    """Compare the simulated dam-tip series against the experiment.

    Args:
        csv_path: CSV written by measure_tool (same points as passed here).
        points: explicit points ``[[x, y, z], ...]`` in CSV column order.
        points_file: MeasureTool points file (POINTS/POINTSLIST/POINTSENDLIST).
        threshold: wetness threshold on the interpolated value (500 kg/m^3
            for density; ~0.05 for velocity magnitude).
        column_length: water-column length ``a`` used for normalisation (m).
        experiment: embedded experimental series (koshizuka1996).
        max_time: ignore simulated samples beyond this time (s).
    """
    if (points is None) == (points_file is None):
        raise BadInputError("pass exactly one of points / points_file")

    path = Path(csv_path).expanduser()
    if not path.is_file():
        raise BadInputError(f"measurement CSV not found: {path}")
    rows = read_measure_csv(path)
    if not rows:
        raise BadInputError(f"no data rows parsed from {path}")

    if points is not None:
        point_tuples = [(p[0], p[1], p[2]) for p in points]
    else:
        assert points_file is not None
        point_tuples = parse_points_file(Path(points_file).expanduser())

    for time_value, values in rows:
        if len(values) != len(point_tuples):
            raise BadInputError(
                f"CSV column count mismatch: row t={time_value} has {len(values)} "
                f"value columns but {len(point_tuples)} points were given "
                "(pass the same points definition that produced the CSV)"
            )

    if max_time is not None:
        rows = [(t, v) for t, v in rows if t <= max_time]

    front_series = extract_front_series(rows, point_tuples, threshold)
    if not front_series:
        raise BadInputError(
            f"no row reached the wetness threshold {threshold}; "
            "check the variable (rhop ~1000 inside the fluid) and the threshold"
        )

    experiment_series = load_experiment(experiment)
    samples: list[dict[str, float]] = []
    for time_value, front_x in front_series:
        expected = _interp(experiment_series, time_value)
        if expected is None:
            continue
        samples.append(
            {
                "time_s": round(time_value, 6),
                "sim_front_m": round(front_x, 6),
                "exp_front_m": round(expected, 6),
                "error_m": round(front_x - expected, 6),
                "error_pct_of_column": round((front_x - expected) / column_length * 100.0, 4),
            }
        )
    if not samples:
        raise BadInputError(
            "simulated times do not overlap the experimental window "
            f"[{experiment_series[0][0]:.3f}, {experiment_series[-1][0]:.3f}] s"
        )

    errors = [abs(sample["error_m"]) for sample in samples]
    mae = sum(errors) / len(errors)
    rmse = (sum(error * error for error in errors) / len(errors)) ** 0.5

    # Wall-impact saturation: either the front literally reaches the last
    # point, or it plateaus at its series maximum for several samples in a
    # row (the tank end: fluid piles against the wall, the furthest
    # interpolable point stops short of the wall by ~one spacing).
    max_point_x = max(p[0] for p in point_tuples)
    max_front = max(front for _, front in front_series)
    impact_time: float | None = None
    plateau_run = 0
    plateau_start: float | None = None
    for time_value, front in front_series:
        if front >= max_front - 1e-9:
            if plateau_run == 0:
                plateau_start = time_value
            plateau_run += 1
            if plateau_run >= 3 and impact_time is None:
                impact_time = plateau_start
        else:
            plateau_run = 0
    reached_last_point = any(s["sim_front_m"] >= max_point_x - 1e-9 for s in samples)
    saturated = reached_last_point or impact_time is not None

    return {
        "experiment": experiment,
        "experiment_reference": (
            "Koshizuka & Oka (1996), Nuclear Science and Engineering 123, 421-434; "
            "digitised series shipped with DualSPHysics examples/main/01_DamBreak"
        ),
        "n_samples": len(samples),
        "threshold": threshold,
        "column_length_m": column_length,
        "mae_m": round(mae, 6),
        "rmse_m": round(rmse, 6),
        "max_abs_error_m": round(max(errors), 6),
        "mae_pct_of_column": round(mae / column_length * 100.0, 4),
        "front_series": samples,
        "impact_time_s": impact_time,
        "notes": (
            (
                "front reached the end of the measured line "
                + (f"and plateaued from t={impact_time:.3f} s " if impact_time else "")
                + "(wall impact); later experimental samples are outside the "
                "comparable window"
            )
            if saturated
            else None
        ),
    }
