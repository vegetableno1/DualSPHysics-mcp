#!/usr/bin/env python3
"""Render a DualSPHysics 2D dam-break animation: Part_*.vtk -> MP4.

Reads the particle VTK sequence written by PartVTK (``partvtk`` with
``-savevtk particles/PartFluid``), draws each frame as a 2D scatter in the
x-z plane coloured by the selected variable (default the velocity magnitude
|v|, which makes the collapse, the surge front and the wall impact legible),
and encodes the PNG frames into an MP4 with imageio-ffmpeg's bundled ffmpeg
binary (no system ffmpeg required).

Typical use for the 2D validation dam-break (201 output parts, 20,000 fluid
particles, TimeOut=0.01 s)::

    # after partvtk produced particles/PartFluid_*.vtk inside the job dir:
    python examples/render_dambreak.py \\
        --dirdata <job_dir>/particles --every 2 --fps 24 --tout 0.01 \\
        --out dambreak_animation.mp4

Viewport cropping: ``--xmin/--xmax/--zmin/--zmax`` restrict an axis to the
action region instead of the full data extent (each is optional and defaults
to the auto limit). Cropping the empty sky above the tank makes the water
fill the frame, e.g. for a 5 s run (~501 parts)::

    python examples/render_dambreak.py \\
        --dirdata <job_dir>/particles --every 4 --fps 24 --tout 0.01 \\
        --zmin -0.4 --zmax 2.1 --out dambreak_animation_5s.mp4

Heavy dependencies (pyvista for VTK reading, matplotlib for drawing,
imageio-ffmpeg for encoding) are imported lazily and each comes with a clear
install hint when missing. Frame selection (--every/--first/--last) and VTK
discovery are pure functions, so they work without any of them.

Dev dependencies only: ``uv add --dev pyvista imageio-ffmpeg matplotlib``
(runtime dependencies of the MCP server stay mcp + pydantic).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # numpy ships with pyvista/matplotlib; annotations only here
    import numpy as np

#: Trailing frame index of a PartVTK output file, e.g. ``PartFluid_0007.vtk``.
_FRAME_RE = re.compile(r"_(\d+)\.vtk$")

_VAR_CHOICES = ("vel", "rhop", "press")
_VAR_LABELS = {"vel": "|v| (m/s)", "rhop": "density (kg/m$^3$)", "press": "pressure (Pa)"}

_INSTALL_HINTS = {
    "pyvista": "uv add --dev pyvista   (or: pip install pyvista)",
    "matplotlib": "uv add --dev matplotlib   (or: pip install matplotlib)",
    "imageio_ffmpeg": "uv add --dev imageio-ffmpeg   (or: pip install imageio-ffmpeg)",
}


class RenderError(RuntimeError):
    """User-facing render failure (missing dependency, no frames, bad args)."""


def _import(name: str) -> ModuleType:
    try:
        return __import__(name)
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatched sys.modules
        raise RenderError(
            f"{name.replace('_', '-')} is required to render animations; install it with: "
            f"{_INSTALL_HINTS[name]}"
        ) from exc


def frame_number(path: Path) -> int:
    """Frame index encoded in a PartVTK output filename (-1 when absent)."""
    match = _FRAME_RE.search(path.name)
    return int(match.group(1)) if match else -1


def find_vtk_files(dirdata: Path) -> list[Path]:
    """Particle VTK frames in ``dirdata`` sorted by frame index.

    Only files ending in ``_<digits>.vtk`` are kept, so GenCase previews
    (``Case_All.vtk``) and non-particle files are ignored.
    """
    if not dirdata.is_dir():
        raise RenderError(f"VTK directory not found: {dirdata}")
    frames = [p for p in dirdata.glob("*.vtk") if frame_number(p) >= 0]
    return sorted(frames, key=frame_number)


def select_frames(
    files: list[Path],
    every: int = 1,
    first: int | None = None,
    last: int | None = None,
) -> list[Path]:
    """Subselect frames: every N-th, optionally clipped to a frame-index range.

    ``first``/``last`` are inclusive frame indices as encoded in the filenames
    (not positions in the list), so ``--first 20`` keeps ``PartFluid_0020.vtk``
    whatever the spacing.
    """
    if every < 1:
        raise RenderError(f"--every must be >= 1, got {every}")
    selected = [
        f
        for f in files
        if (first is None or frame_number(f) >= first) and (last is None or frame_number(f) <= last)
    ]
    return selected[::every]


def speed_magnitude(vel: object) -> np.ndarray:
    """Per-particle velocity magnitude from an (N, 3) array."""
    import numpy as np

    return np.linalg.norm(np.asarray(vel, dtype=np.float64), axis=1)


def read_frame(path: Path, variables: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read one VTK frame -> (x, z, scalar) float arrays for the x-z plane.

    ``variables`` selects the scalar: ``vel`` (|v| magnitude), ``rhop`` or
    ``press`` — the names PartVTK stores with ``-vars:+idp,+vel,+rhop``.
    """
    pv = _import("pyvista")
    mesh = pv.read(str(path))
    points = mesh.points
    if variables == "vel":
        if "Vel" not in mesh.point_data:
            raise RenderError(
                f"{path.name}: no 'Vel' array (re-run partvtk with -vars:+vel or use --vars rhop)"
            )
        scalars = speed_magnitude(mesh.point_data["Vel"])
    else:
        key = {"rhop": "Rhop", "press": "Press"}[variables]
        if key not in mesh.point_data:
            raise RenderError(
                f"{path.name}: no '{key}' array (re-run partvtk with -vars:+{variables})"
            )
        scalars = mesh.point_data[key]
    import numpy as np

    return (
        np.asarray(points[:, 0], dtype=np.float64),
        np.asarray(points[:, 2], dtype=np.float64),
        np.asarray(scalars, dtype=np.float64),
    )


def _axis_limits(values: list[np.ndarray], pad_frac: float = 0.03) -> tuple[float, float]:
    import numpy as np

    lo = float(np.min([v.min() for v in values]))
    hi = float(np.max([v.max() for v in values]))
    span = max(hi - lo, 1e-6)
    return lo - span * pad_frac, hi + span * pad_frac


def check_viewport(
    xmin: float | None, xmax: float | None, zmin: float | None, zmax: float | None
) -> None:
    """Reject inverted/degenerate viewport pairs before any VTK is read.

    Each check is skipped while one side is None (a mixed window like
    ``--zmin -0.4`` alone is legal; the open side stays auto and is only
    comparable once the data extent is known — see :func:`viewport_limits`).
    NaN comparisons are False, so non-finite limits fail here too.
    """
    for lo_name, lo, hi_name, hi in (("xmin", xmin, "xmax", xmax), ("zmin", zmin, "zmax", zmax)):
        if lo is not None and hi is not None and not lo < hi:
            raise RenderError(
                f"--{lo_name} must be < --{hi_name}, got {lo:g} vs {hi:g} "
                "(viewport min/max are inverted or not finite)"
            )


def viewport_limits(
    values: list[np.ndarray],
    lo: float | None,
    hi: float | None,
    pad_frac: float = 0.03,
) -> tuple[float, float]:
    """Axis window for one axis: auto extent with optional per-side overrides.

    An explicit limit replaces the auto one VERBATIM (no padding added), so
    ``--zmin -0.4 --zmax 2.1`` crops z to exactly that band while the
    untouched axes keep the padded full-data extent.
    """
    auto_lo, auto_hi = _axis_limits(values, pad_frac)
    eff_lo = auto_lo if lo is None else float(lo)
    eff_hi = auto_hi if hi is None else float(hi)
    if not eff_lo < eff_hi:
        raise RenderError(
            f"viewport min must be < max after overrides, got [{eff_lo:g}, {eff_hi:g}]"
        )
    return eff_lo, eff_hi


def render_animation(
    dirdata: Path,
    out: Path,
    fps: int = 24,
    every: int = 2,
    variables: str = "vel",
    first: int | None = None,
    last: int | None = None,
    tout: float | None = None,
    vmax: float | None = None,
    dpi: int = 100,
    xmin: float | None = None,
    xmax: float | None = None,
    zmin: float | None = None,
    zmax: float | None = None,
) -> Path:
    """Render the selected frames and encode them; returns the MP4 path."""
    check_viewport(xmin, xmax, zmin, zmax)  # cheap sanity before reading any VTK
    files = select_frames(find_vtk_files(dirdata), every=every, first=first, last=last)
    if not files:
        raise RenderError(f"no Part_*.vtk frames matched in {dirdata} (adjust --first/--last)")

    print(f"reading {len(files)} frames from {dirdata} (every={every}, vars={variables})")
    frames = [read_frame(path, variables) for path in files]

    import numpy as np

    scalars = np.concatenate([s for _, _, s in frames])
    vmin = 0.0 if variables == "vel" else float(scalars.min())
    vmax_eff = float(vmax) if vmax is not None else float(np.percentile(scalars, 99.5))
    print(f"colour scale: [{vmin:.3f}, {vmax_eff:.3f}] ({_VAR_LABELS[variables]})")

    matplotlib = _import("matplotlib")
    matplotlib.use("Agg")  # headless-safe: no display is required anywhere
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    x_lo, x_hi = viewport_limits([x for x, _, _ in frames], xmin, xmax)
    z_lo, z_hi = viewport_limits([z for _, z, _ in frames], zmin, zmax)
    span_ratio = (z_hi - z_lo) / max(x_hi - x_lo, 1e-9)
    fig_w = 12.0
    fig_h = min(max(fig_w * span_ratio * 1.12, 4.0), 12.0)  # colorbar row + margins
    # yuv420p encoding needs even pixel dimensions: nudge the figure size by
    # one pixel until the canvas is even (float rounding makes pre-computing
    # the pixel size unreliable).
    fig = ax = None
    for _ in range(4):
        candidate_fig, candidate_ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
        canvas_w, canvas_h = candidate_fig.canvas.get_width_height()
        if canvas_w % 2 == 0 and canvas_h % 2 == 0:
            fig, ax = candidate_fig, candidate_ax
            break
        plt.close(candidate_fig)
        if canvas_w % 2:
            fig_w += 1.0 / dpi
        if canvas_h % 2:
            fig_h += 1.0 / dpi
    if fig is None or ax is None:
        raise RenderError(f"could not build an even-sized canvas at dpi={dpi}")
    fig.subplots_adjust(left=0.06, right=0.92, top=0.90, bottom=0.07)

    first_x, first_z, first_s = frames[0]
    scatter = ax.scatter(
        first_x, first_z, c=first_s, s=4, cmap="turbo", vmin=vmin, vmax=vmax_eff, lw=0
    )
    colorbar = fig.colorbar(scatter, ax=ax, pad=0.01)
    colorbar.set_label(_VAR_LABELS[variables])
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(z_lo, z_hi)
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("z (m)")
    ax.grid(alpha=0.15, lw=0.4)

    imageio_ffmpeg = _import("imageio_ffmpeg")
    canvas = fig.canvas
    assert isinstance(canvas, FigureCanvasAgg)  # Agg was selected above; buffer_rgba exists there
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(out),
        size=(canvas_w, canvas_h),
        fps=fps,
        codec="libx264",
        pix_fmt_in="rgb24",
        macro_block_size=1,
    )
    writer.send(None)  # seed the generator protocol
    try:
        for index, ((x, z, s), path) in enumerate(zip(frames, files, strict=True)):
            offsets = np.empty((x.size, 2))
            offsets[:, 0] = x
            offsets[:, 1] = z
            scatter.set_offsets(offsets)
            scatter.set_array(s)
            frame_no = frame_number(path)
            title = f"t = {frame_no * tout:.2f} s" if tout is not None else f"frame {frame_no}"
            ax.set_title(
                f"DualSPHysics 2D dam-break — {title}   "
                f"({x.size:,} fluid particles, coloured by {_VAR_LABELS[variables]})",
                fontsize=11,
            )
            fig.canvas.draw()
            rgba = np.asarray(canvas.buffer_rgba())
            writer.send(np.ascontiguousarray(rgba[:, :, :3]).tobytes())
            if index % 25 == 0:
                print(f"  encoded {index + 1}/{len(frames)} frames")
    finally:
        writer.close()
    plt.close(fig)
    print(f"wrote {out} ({len(frames)} frames @ {fps} fps)")
    return out


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_dambreak.py",
        description="Render DualSPHysics Part_*.vtk particle frames as a 2D x-z MP4 animation.",
    )
    parser.add_argument(
        "--dirdata",
        type=Path,
        required=True,
        help="directory holding the PartVTK output frames (e.g. <job_dir>/particles)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("dambreak_animation.mp4"), help="output MP4 path"
    )
    parser.add_argument("--fps", type=positive_int, default=24, help="playback frame rate")
    parser.add_argument(
        "--every",
        type=positive_int,
        default=2,
        help="take every N-th VTK frame (201 frames x every=2 @ 24 fps ~= 4 s highlight reel)",
    )
    parser.add_argument(
        "--vars",
        choices=_VAR_CHOICES,
        default="vel",
        help="scalar for the colour scale: vel = |velocity| (default), rhop, press",
    )
    parser.add_argument("--first", type=int, default=None, help="first frame index to include")
    parser.add_argument("--last", type=int, default=None, help="last frame index to include")
    parser.add_argument(
        "--tout",
        type=float,
        default=None,
        help="TimeOut of the simulation (s); annotates each frame with t = index x tout",
    )
    parser.add_argument("--vmax", type=float, default=None, help="fixed colour-scale maximum")
    parser.add_argument("--dpi", type=positive_int, default=100, help="render resolution")
    parser.add_argument(
        "--xmin",
        type=float,
        default=None,
        help="viewport: lower x limit (default: auto from the particle data)",
    )
    parser.add_argument(
        "--xmax",
        type=float,
        default=None,
        help="viewport: upper x limit (default: auto from the particle data)",
    )
    parser.add_argument(
        "--zmin",
        type=float,
        default=None,
        help="viewport: lower z limit, e.g. -0.4 to crop below the tank floor",
    )
    parser.add_argument(
        "--zmax",
        type=float,
        default=None,
        help="viewport: upper z limit, e.g. 2.1 to crop the empty sky above the action",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        render_animation(
            dirdata=args.dirdata,
            out=args.out,
            fps=args.fps,
            every=args.every,
            variables=args.vars,
            first=args.first,
            last=args.last,
            tout=args.tout,
            vmax=args.vmax,
            dpi=args.dpi,
            xmin=args.xmin,
            xmax=args.xmax,
            zmin=args.zmin,
            zmax=args.zmax,
        )
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
