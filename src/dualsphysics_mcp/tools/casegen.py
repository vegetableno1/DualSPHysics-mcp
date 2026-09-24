"""casegen: create / edit / describe GenCase case XML (2D dam-break family).

Pure Python — no solver involved. The vocabulary covers the 2D dam-break
family in the XZ plane (y pinned to 0, z vertical):

- water column (reservoir) at the origin: ``column_length x column_height``
- open-top tank: ``tank_length x tank_height`` (bottom + left + right walls)
- optional downstream obstacle: solid box standing on the tank floor
- SWL gauges: vertical probes (fixed x, scanning z) and horizontal probes
  (fixed z, scanning x)
- dp, domain margins, physics constants (gravity / rhop0 / cfl / visco)
  and time control (TimeMax / TimeOut)

Guards derived from the measured GenCase v5.4 XML contract:

- geometry outside the domain box is **silently clipped** (exit 0, no
  warning) — the domain is derived from the tank plus strictly positive
  margins, and an explicit ``domain`` override must strictly contain the
  geometry, otherwise DSPH_BAD_INPUT;
- ``<setdrawmode mode="full"/>`` is always emitted (without it the fluid
  loses its boundary lattice row: 20,000 -> 19,701 particles);
- 2D means pointmin.y == pointmax.y == 0; all geometry spans y in [-1, 1]
  so it crosses the y=0 plane (geometry missing the plane yields zero
  particles, again silently);
- particle counts are estimated with lattice arithmetic measured against
  GenCase v5.4 and labelled as estimates (GenCase's own particle summary
  is authoritative).

Validation order (project iron rule): pure parameter validation of the
overrides runs BEFORE any filesystem access, so bad numbers report as
DSPH_BAD_INPUT even when the output path is unwritable or the case file
does not exist.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import BadInputError

GAUGE_KINDS = ("vertical", "horizontal")

#: DualSPHysics case files often carry "--" inside XML comments (illegal XML
#: that GenCase tolerates); comments carry no case data, so strip them first.
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: Keys accepted in ``overrides`` (and their nested sub-keys).
_MARGIN_KEYS = ("x_min", "z_min", "x_max", "z_max")
_SCALAR_KEYS = (
    "dp",
    "column_length",
    "column_height",
    "tank_length",
    "tank_height",
    "gravity",
    "rhop0",
    "cfl",
    "visco",
    "time_max",
    "time_out",
)
ALLOWED_OVERRIDE_KEYS = frozenset([*_SCALAR_KEYS, "obstacle", "domain", "margins", "gauges"])

ESTIMATE_NOTE = (
    "Lattice arithmetic measured against GenCase v5.4: fluid box "
    "round(L/dp) per axis; open-top tank shell (round(L/dp)+1) + 2*round(H/dp); "
    "solid obstacle (round(w/dp)+1)*(round(h/dp)+1) replacing its floor row. "
    "GenCase's own particle summary is authoritative; sizes that are not "
    "integer multiples of dp (or an obstacle touching the water column edge) "
    "can shift counts by a few particles."
)


# ---------------------------------------------------------------------------
# Templates (data, not code: adding a template = adding an entry here)
# ---------------------------------------------------------------------------

#: Baseline reproduces the official CaseDambreakVal2D layout: dp=0.01,
#: 1x2 m column in a 4x3 m tank, domain (-1,0,-1)..(4.5,0,3.5), TimeMax=2 s,
#: TimeOut=0.01 s, gauges at x=0.2 (vertical) and z=0.03 (horizontal).
#: GenCase-measured counts: fluid 20,000 + bound 1,001 = 21,001.
DAMBREAK_VAL2D_DEFAULTS: dict[str, Any] = {
    "dp": 0.01,
    "column_length": 1.0,
    "column_height": 2.0,
    "tank_length": 4.0,
    "tank_height": 3.0,
    "margins": {"x_min": 1.0, "z_min": 1.0, "x_max": 0.5, "z_max": 0.5},
    "gravity": 9.81,
    "rhop0": 1000,
    "cfl": 0.2,
    "visco": 0.02,
    "time_max": 2.0,
    "time_out": 0.01,
    "obstacle": None,
    "gauges": [
        {"name": "Swl_x02", "type": "vertical", "x": 0.2},
        {"name": "Swl_z003", "type": "horizontal", "z": 0.03},
    ],
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "dambreak_val2d": {
        "description": (
            "2D dam-break validation layout (Koshizuka & Oka 1996 family): "
            "rectangular water column at the origin inside an open-top tank, "
            "optional downstream obstacle, SWL gauges."
        ),
        "defaults": DAMBREAK_VAL2D_DEFAULTS,
    }
}


# ---------------------------------------------------------------------------
# Case model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Obstacle:
    """Solid box standing on the tank floor (2D: x position, width, height)."""

    x: float
    width: float
    height: float


@dataclass(frozen=True)
class Gauge:
    """SWL gauge. Vertical: fixed ``x``, probes z in [-0.05, z_top].

    Horizontal: fixed ``z``, probes x in [-0.05, x_end].
    """

    kind: str
    x: float | None = None
    z: float | None = None
    name: str | None = None
    z_top: float | None = None
    x_end: float | None = None


@dataclass(frozen=True)
class CaseParams:
    """Full parameter set of the 2D dam-break family vocabulary."""

    dp: float
    column_length: float
    column_height: float
    tank_length: float
    tank_height: float
    gravity: float
    rhop0: int
    cfl: float
    visco: float
    time_max: float
    time_out: float
    margins: dict[str, float] = field(default_factory=dict)
    domain: tuple[tuple[float, float], tuple[float, float]] | None = None
    obstacle: Obstacle | None = None
    gauges: tuple[Gauge, ...] = ()


def effective_domain(p: CaseParams) -> tuple[tuple[float, float], tuple[float, float]]:
    """((xmin, zmin), (xmax, zmax)) — explicit override or tank + margins."""
    if p.domain is not None:
        return p.domain
    m = p.margins
    return (
        (-m["x_min"], -m["z_min"]),
        (p.tank_length + m["x_max"], p.tank_height + m["z_max"]),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _as_number(container: str, key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadInputError(f"{container} {key!r} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise BadInputError(f"{container} {key!r} must be finite, got {value!r}")
    return number


def _as_int(container: str, key: str, value: Any) -> int:
    number = _as_number(container, key, value)
    if number != int(number):
        raise BadInputError(f"{container} {key!r} must be an integer, got {value!r}")
    return int(number)


def _require_positive(container: str, key: str, value: float) -> None:
    if value <= 0:
        raise BadInputError(f"{container} {key!r} must be > 0, got {value:g}")


def _check_pair(name: str, pair: Any) -> tuple[float, float]:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise BadInputError(f"{name} must be a [x, z] pair, got {pair!r}")
    return (_as_number(name, "x", pair[0]), _as_number(name, "z", pair[1]))


def _build_gauge(spec: Any, index: int) -> Gauge:
    where = f"gauges[{index}]"
    if not isinstance(spec, dict):
        raise BadInputError(f"{where} must be an object, got {spec!r}")
    unknown = set(spec) - {"type", "name", "x", "z", "z_top", "x_end"}
    if unknown:
        raise BadInputError(
            f"{where} has unknown key(s) {sorted(unknown)}; allowed: type, name, x, z, z_top, x_end"
        )
    kind = spec.get("type")
    if kind not in GAUGE_KINDS:
        raise BadInputError(f"{where} type must be one of {list(GAUGE_KINDS)}, got {kind!r}")
    if "name" in spec:
        name = spec["name"]
        if not isinstance(name, str) or not name.strip():
            raise BadInputError(f"{where} name must be a non-empty string, got {name!r}")
    if kind == "vertical":
        if "x" not in spec:
            raise BadInputError(f"{where} vertical gauge requires 'x'")
        if "z" in spec or "x_end" in spec:
            raise BadInputError(f"{where} vertical gauge takes 'x'/'z_top', not 'z'/'x_end'")
        return Gauge(
            kind=kind,
            x=_as_number(where, "x", spec["x"]),
            name=spec.get("name"),
            z_top=_as_number(where, "z_top", spec["z_top"]) if "z_top" in spec else None,
        )
    if "z" not in spec:
        raise BadInputError(f"{where} horizontal gauge requires 'z'")
    if "x" in spec or "z_top" in spec:
        raise BadInputError(f"{where} horizontal gauge takes 'z'/'x_end', not 'x'/'z_top'")
    return Gauge(
        kind=kind,
        z=_as_number(where, "z", spec["z"]),
        name=spec.get("name"),
        x_end=_as_number(where, "x_end", spec["x_end"]) if "x_end" in spec else None,
    )


def validate_overrides_static(overrides: dict[str, Any]) -> None:
    """Pure-parameter validation: unknown keys, types, standalone ranges.

    Runs before any filesystem access (project iron rule) — every failure
    here is DSPH_BAD_INPUT regardless of toolchain or paths.
    """
    unknown = set(overrides) - ALLOWED_OVERRIDE_KEYS
    if unknown:
        raise BadInputError(
            f"unknown override key(s) {sorted(unknown)}; allowed: {sorted(ALLOWED_OVERRIDE_KEYS)}"
        )
    for key in _SCALAR_KEYS:
        if key not in overrides:
            continue
        value = _as_number("override", key, overrides[key])
        if key in ("visco",):
            if value < 0:
                raise BadInputError(f"override {key!r} must be >= 0, got {value:g}")
        elif key == "cfl":
            if not 0 < value <= 1:
                raise BadInputError(f"override {key!r} must be in (0, 1], got {value:g}")
        else:
            _require_positive("override", key, value)

    obstacle = overrides.get("obstacle")
    if obstacle is not None:
        if not isinstance(obstacle, dict):
            raise BadInputError(f"override 'obstacle' must be an object or null, got {obstacle!r}")
        unknown = set(obstacle) - {"x", "width", "height"}
        if unknown:
            raise BadInputError(
                f"obstacle has unknown key(s) {sorted(unknown)}; allowed: x, width, height"
            )
        for key in ("x", "width", "height"):
            if key not in obstacle:
                raise BadInputError(f"obstacle requires {key!r}")
            _require_positive("obstacle", key, _as_number("obstacle", key, obstacle[key]))

    margins = overrides.get("margins")
    if margins is not None:
        if not isinstance(margins, dict):
            raise BadInputError(f"override 'margins' must be an object, got {margins!r}")
        unknown = set(margins) - set(_MARGIN_KEYS)
        if unknown:
            raise BadInputError(
                f"margins has unknown key(s) {sorted(unknown)}; allowed: {list(_MARGIN_KEYS)}"
            )
        for key, value in margins.items():
            _require_positive("margins", key, _as_number("margins", key, value))

    domain = overrides.get("domain")
    if domain is not None:
        if not isinstance(domain, dict) or set(domain) != {"pointmin", "pointmax"}:
            raise BadInputError(
                "override 'domain' must be {'pointmin': [x, z], 'pointmax': [x, z]}, "
                f"got {domain!r}"
            )
        _check_pair("domain pointmin", domain["pointmin"])
        _check_pair("domain pointmax", domain["pointmax"])

    gauges = overrides.get("gauges")
    if gauges is not None:
        if not isinstance(gauges, list):
            raise BadInputError(f"override 'gauges' must be a list, got {gauges!r}")
        for index, spec in enumerate(gauges):
            _build_gauge(spec, index)


def validate_params(p: CaseParams) -> None:
    """Full (relative) validation of a merged parameter set.

    Covers everything the static pass cannot see: geometry containment,
    domain containment (anti silent-clipping guard), gauge placement and
    time ordering. Also used to re-validate parsed files in edit_case.
    """
    _require_positive("case", "dp", p.dp)
    for key in ("column_length", "column_height", "tank_length", "tank_height"):
        _require_positive("case", key, getattr(p, key))
    if p.column_length > p.tank_length:
        raise BadInputError(
            f"column_length ({p.column_length:g}) must be <= tank_length ({p.tank_length:g})"
        )
    if p.column_height > p.tank_height:
        raise BadInputError(
            f"column_height ({p.column_height:g}) must be <= tank_height ({p.tank_height:g})"
        )
    for key in ("column_length", "column_height", "tank_length", "tank_height"):
        if getattr(p, key) < p.dp:
            raise BadInputError(
                f"{key} ({getattr(p, key):g}) must be at least dp ({p.dp:g}); "
                "thinner geometry cannot hold a lattice row"
            )

    for key, value in p.margins.items():
        _require_positive("margins", key, value)
    (xmin, zmin), (xmax, zmax) = effective_domain(p)
    # Anti silent-clipping guard: GenCase clips geometry outside the domain
    # box with exit code 0 and no warning, so the domain must STRICTLY
    # contain the geometry (the tank is the outermost box; equality also
    # clips — the boundary lattice row is dropped).
    problems = []
    if not xmin < 0:
        problems.append(f"pointmin x={xmin:g} must be < 0")
    if not zmin < 0:
        problems.append(f"pointmin z={zmin:g} must be < 0")
    if not xmax > p.tank_length:
        problems.append(
            f"pointmax x={xmax:g} must be > tank_length={p.tank_length:g} "
            "(geometry outside the domain is silently clipped by GenCase)"
        )
    if not zmax > p.tank_height:
        problems.append(
            f"pointmax z={zmax:g} must be > tank_height={p.tank_height:g} "
            "(geometry outside the domain is silently clipped by GenCase)"
        )
    if problems:
        raise BadInputError("domain does not contain the geometry: " + "; ".join(problems))

    if p.obstacle is not None:
        o = p.obstacle
        for key in ("width", "height"):
            _require_positive("obstacle", key, getattr(o, key))
        if o.width < p.dp or o.height < p.dp:
            raise BadInputError(f"obstacle width/height must be at least dp ({p.dp:g})")
        if o.x < p.column_length:
            raise BadInputError(
                f"obstacle x ({o.x:g}) must be >= column_length ({p.column_length:g}); "
                "an obstacle inside the initial water column is not supported"
            )
        if o.x + o.width > p.tank_length:
            raise BadInputError(
                f"obstacle exceeds the tank: x + width = {o.x + o.width:g} > "
                f"tank_length = {p.tank_length:g}"
            )
        if o.height > p.tank_height:
            raise BadInputError(
                f"obstacle height ({o.height:g}) must be <= tank_height ({p.tank_height:g})"
            )

    if not 0 < p.time_out <= p.time_max:
        raise BadInputError(f"time_out ({p.time_out:g}) must be in (0, time_max={p.time_max:g}]")
    if not 0 < p.cfl <= 1:
        raise BadInputError(f"cfl ({p.cfl:g}) must be in (0, 1]")
    if p.gravity <= 0:
        raise BadInputError(f"gravity ({p.gravity:g}) must be > 0")
    if p.rhop0 <= 0:
        raise BadInputError(f"rhop0 ({p.rhop0}) must be > 0")
    if p.visco < 0:
        raise BadInputError(f"visco ({p.visco:g}) must be >= 0")

    for index, g in enumerate(p.gauges):
        where = f"gauges[{index}]"
        if g.kind == "vertical":
            if g.x is None:
                raise BadInputError(f"{where} vertical gauge requires x")
            if not 0 <= g.x <= p.tank_length:
                raise BadInputError(
                    f"{where} vertical gauge x ({g.x:g}) must be within "
                    f"[0, tank_length={p.tank_length:g}]"
                )
            z_top = p.column_height + 0.1 if g.z_top is None else g.z_top
            if z_top <= p.column_height:
                raise BadInputError(
                    f"{where} vertical gauge z_top ({z_top:g}) must be above the "
                    f"column height ({p.column_height:g}) to see the free surface"
                )
        else:
            if g.z is None:
                raise BadInputError(f"{where} horizontal gauge requires z")
            if not 0 <= g.z <= p.tank_height:
                raise BadInputError(
                    f"{where} horizontal gauge z ({g.z:g}) must be within "
                    f"[0, tank_height={p.tank_height:g}]"
                )
            x_end = p.tank_length + 0.05 if g.x_end is None else g.x_end
            if x_end <= 0:
                raise BadInputError(f"{where} horizontal gauge x_end ({x_end:g}) must be > 0")


# ---------------------------------------------------------------------------
# Merge / build
# ---------------------------------------------------------------------------


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if key == "margins" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged["margins"] = {**merged["margins"], **value}
        else:
            merged[key] = value
    return merged


def _build_obstacle(spec: Any) -> Obstacle | None:
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise BadInputError(f"obstacle must be an object or null, got {spec!r}")
    return Obstacle(
        x=_as_number("obstacle", "x", spec.get("x")),
        width=_as_number("obstacle", "width", spec.get("width")),
        height=_as_number("obstacle", "height", spec.get("height")),
    )


def _build_domain(spec: Any) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if spec is None:
        return None
    pmin = _check_pair("domain pointmin", spec["pointmin"])
    pmax = _check_pair("domain pointmax", spec["pointmax"])
    return (pmin, pmax)


def _build_params(merged: dict[str, Any]) -> CaseParams:
    obstacle = _build_obstacle(merged.get("obstacle"))
    gauges = tuple(_build_gauge(spec, i) for i, spec in enumerate(merged.get("gauges") or []))
    params = CaseParams(
        dp=_as_number("case", "dp", merged["dp"]),
        column_length=_as_number("case", "column_length", merged["column_length"]),
        column_height=_as_number("case", "column_height", merged["column_height"]),
        tank_length=_as_number("case", "tank_length", merged["tank_length"]),
        tank_height=_as_number("case", "tank_height", merged["tank_height"]),
        gravity=_as_number("case", "gravity", merged["gravity"]),
        rhop0=_as_int("case", "rhop0", merged["rhop0"]),
        cfl=_as_number("case", "cfl", merged["cfl"]),
        visco=_as_number("case", "visco", merged["visco"]),
        time_max=_as_number("case", "time_max", merged["time_max"]),
        time_out=_as_number("case", "time_out", merged["time_out"]),
        margins={
            key: _as_number("margins", key, value)
            for key, value in (merged.get("margins") or {}).items()
        },
        domain=_build_domain(merged.get("domain")),
        obstacle=obstacle,
        gauges=gauges,
    )
    return _autoname(params)


def _params_to_base(p: CaseParams) -> dict[str, Any]:
    """CaseParams -> template-shaped dict (round-trip base for edit_case)."""
    base: dict[str, Any] = {
        "dp": p.dp,
        "column_length": p.column_length,
        "column_height": p.column_height,
        "tank_length": p.tank_length,
        "tank_height": p.tank_height,
        "gravity": p.gravity,
        "rhop0": p.rhop0,
        "cfl": p.cfl,
        "visco": p.visco,
        "time_max": p.time_max,
        "time_out": p.time_out,
        "margins": dict(p.margins),
        "obstacle": None
        if p.obstacle is None
        else {"x": p.obstacle.x, "width": p.obstacle.width, "height": p.obstacle.height},
        "gauges": [
            {
                k: v
                for k, v in (
                    ("name", g.name),
                    ("type", g.kind),
                    ("x", g.x),
                    ("z", g.z),
                    ("z_top", g.z_top),
                    ("x_end", g.x_end),
                )
                if v is not None
            }
            for g in p.gauges
        ],
    }
    if p.domain is not None:
        base["domain"] = {
            "pointmin": [p.domain[0][0], p.domain[0][1]],
            "pointmax": [p.domain[1][0], p.domain[1][1]],
        }
    return base


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    """Compact fixed-point XML number (no scientific notation)."""
    if value == int(value) and abs(value) < 1e16:
        return str(int(value))
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in ("", "-"):
        text = "0"
    if float(text) == 0 and value != 0:  # too small for fixed notation
        return repr(value)
    return text


def _sanitize_name(name: str, fallback: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in name.strip())
    return cleaned or fallback


def _autoname(params: CaseParams) -> CaseParams:
    """Resolve gauge auto-names and effective z_top / x_end values.

    Explicit duplicate names are an error; auto-generated names get a ``_``
    suffix until unique.
    """
    resolved: list[Gauge] = []
    names: set[str] = set()
    for g in params.gauges:
        if g.kind == "vertical" and g.x is not None:
            tag = _fmt(g.x).replace(".", "p").replace("-", "m")
            fallback = f"Swl_x{tag}"
            z_top = params.column_height + 0.1 if g.z_top is None else g.z_top
            name = _sanitize_name(g.name or "", fallback)
            while name in names:
                if g.name:  # explicit duplicates are a user error
                    raise BadInputError(f"duplicate gauge name {g.name!r}")
                name += "_"
            names.add(name)
            resolved.append(Gauge(kind="vertical", x=g.x, name=name, z_top=z_top))
        elif g.z is not None:
            tag = _fmt(g.z).replace(".", "p").replace("-", "m")
            fallback = f"Swl_z{tag}"
            x_end = params.tank_length + 0.05 if g.x_end is None else g.x_end
            name = _sanitize_name(g.name or "", fallback)
            while name in names:
                if g.name:  # explicit duplicates are a user error
                    raise BadInputError(f"duplicate gauge name {g.name!r}")
                name += "_"
            names.add(name)
            resolved.append(Gauge(kind="horizontal", z=g.z, name=name, x_end=x_end))
        else:  # pragma: no cover - shape enforced by validation
            raise BadInputError(f"gauge without position: {g!r}")
    return CaseParams(**{**_params_dict(params), "gauges": tuple(resolved)})


def _params_dict(p: CaseParams) -> dict[str, Any]:
    return {
        "dp": p.dp,
        "column_length": p.column_length,
        "column_height": p.column_height,
        "tank_length": p.tank_length,
        "tank_height": p.tank_height,
        "gravity": p.gravity,
        "rhop0": p.rhop0,
        "cfl": p.cfl,
        "visco": p.visco,
        "time_max": p.time_max,
        "time_out": p.time_out,
        "margins": p.margins,
        "domain": p.domain,
        "obstacle": p.obstacle,
        "gauges": p.gauges,
    }


def render_case_xml(p: CaseParams, source: str) -> str:
    """Render the *_Def.xml text for a validated CaseParams."""
    (xmin, zmin), (xmax, zmax) = effective_domain(p)
    lines: list[str] = []
    a = lines.append
    a('<?xml version="1.0" encoding="UTF-8" ?>')
    a(
        f"<!-- 2D dam-break case (dambreak_val2d family). "
        f"Generated by dualsphysics-mcp {__version__} {source.replace('--', '-')}. -->"
    )
    a("<case>")
    a("    <casedef>")
    a("        <constantsdef>")
    a(
        f'            <gravity x="0" y="0" z="{_fmt(-p.gravity)}"'
        ' comment="Gravitational acceleration" units_comment="m/s^2" />'
    )
    a(
        f'            <rhop0 value="{p.rhop0}"'
        ' comment="Reference density of the fluid" units_comment="kg/m^3" />'
    )
    a('            <rhopgradient value="2" comment="Initial density gradient (default=2)" />')
    a('            <hswl value="0" auto="true" comment="Maximum still water level (auto)" />')
    a('            <gamma value="7" comment="Polytropic constant for water" />')
    a('            <speedsystem value="0" auto="true" comment="Maximum system speed (auto)" />')
    a('            <coefsound value="20" comment="Coefficient to multiply speedsystem" />')
    a('            <speedsound value="0" auto="true" comment="Speed of sound (auto)" />')
    a('            <coefh value="1.0" comment="Coefficient to calculate the smoothing length" />')
    a(f'            <cflnumber value="{_fmt(p.cfl)}" comment="Coefficient to multiply dt" />')
    a("        </constantsdef>")
    a('        <mkconfig boundcount="240" fluidcount="9" />')
    a("        <geometry>")
    a(f'            <definition dp="{_fmt(p.dp)}" units_comment="metres (m)">')
    a('                <pointref x="0" y="0" z="0" />')
    a(f'                <pointmin x="{_fmt(xmin)}" y="0" z="{_fmt(zmin)}" />')
    a(f'                <pointmax x="{_fmt(xmax)}" y="0" z="{_fmt(zmax)}" />')
    a("            </definition>")
    a("            <commands>")
    a("                <mainlist>")
    a('                    <setdrawmode mode="full" />')
    a('                    <setmkfluid mk="0" />')
    a("                    <drawbox>")
    a("                        <boxfill>solid</boxfill>")
    a('                        <point x="0" y="-1" z="0" />')
    a(
        f'                        <size x="{_fmt(p.column_length)}" y="2"'
        f' z="{_fmt(p.column_height)}" />'
    )
    a("                    </drawbox>")
    a('                    <setmkbound mk="0" />')
    a("                    <drawbox>")
    a("                        <boxfill>bottom | left | right | front | back</boxfill>")
    a('                        <point x="0" y="-1" z="0" />')
    a(f'                        <size x="{_fmt(p.tank_length)}" y="2" z="{_fmt(p.tank_height)}" />')
    a("                    </drawbox>")
    if p.obstacle is not None:
        o = p.obstacle
        a('                    <setmkbound mk="1" />')
        a("                    <drawbox>")
        a("                        <boxfill>solid</boxfill>")
        a(f'                        <point x="{_fmt(o.x)}" y="-1" z="0" />')
        a(f'                        <size x="{_fmt(o.width)}" y="2" z="{_fmt(o.height)}" />')
        a("                    </drawbox>")
    a("                </mainlist>")
    a("            </commands>")
    a("        </geometry>")
    a("    </casedef>")
    a("    <execution>")
    if p.gauges:
        a("        <special>")
        a("            <gauges>")
        a("                <default>")
        a('                    <savevtkpart value="true" comment="VTK of gauge points per PART" />')
        a('                    <output value="true" comment="CSV of measurements" />')
        a("                </default>")
        for g in p.gauges:
            a(f'                <swl name="{g.name}">')
            if g.kind == "horizontal":
                a('                    <computedt value="0.005" />')
                a('                    <outputdt value="0.005" />')
            a('                    <pointdp coefdp="0.5" />')
            if g.kind == "vertical":
                assert g.x is not None and g.z_top is not None
                a(f'                    <point0 x="{_fmt(g.x)}" y="0" z="-0.05" />')
                a(f'                    <point2 x="{_fmt(g.x)}" y="0" z="{_fmt(g.z_top)}" />')
            else:
                assert g.z is not None and g.x_end is not None
                a(f'                    <point0 x="-0.05" y="0" z="{_fmt(g.z)}" />')
                a(f'                    <point2 x="{_fmt(g.x_end)}" y="0" z="{_fmt(g.z)}" />')
            a("                </swl>")
        a("            </gauges>")
        a("        </special>")
    a("        <parameters>")
    a('            <parameter key="SavePosDouble" value="0" />')
    a('            <parameter key="StepAlgorithm" value="1" comment="1:Verlet, 2:Symplectic" />')
    a('            <parameter key="VerletSteps" value="40" />')
    a('            <parameter key="Kernel" value="2" comment="1:Cubic Spline, 2:Wendland" />')
    a('            <parameter key="ViscoTreatment" value="1" comment="1:Artificial viscosity" />')
    a(f'            <parameter key="Visco" value="{_fmt(p.visco)}" comment="Viscosity value" />')
    a('            <parameter key="ViscoBoundFactor" value="1" />')
    a('            <parameter key="DensityDT" value="2" comment="2:Fourtakas" />')
    a('            <parameter key="DensityDTvalue" value="0.1" />')
    a('            <parameter key="Shifting" value="0" />')
    a('            <parameter key="RigidAlgorithm" value="1" />')
    a('            <parameter key="CoefDtMin" value="0.05" />')
    a('            <parameter key="DtIni" value="0" />')
    a('            <parameter key="DtMin" value="0" />')
    a('            <parameter key="DtFixed" value="0" />')
    a('            <parameter key="DtAllParticles" value="0" />')
    a(
        f'            <parameter key="TimeMax" value="{_fmt(p.time_max)}"'
        ' comment="Time of simulation" units_comment="seconds" />'
    )
    a(
        f'            <parameter key="TimeOut" value="{_fmt(p.time_out)}"'
        ' comment="Time out data" units_comment="seconds" />'
    )
    a('            <parameter key="PartsOutMax" value="1" />')
    a('            <parameter key="RhopOutMin" value="700" units_comment="kg/m^3" />')
    a('            <parameter key="RhopOutMax" value="1300" units_comment="kg/m^3" />')
    a(
        "            <simulationdomain"
        ' comment="Domain of simulation (default=uses min/max position of generated particles)">'
    )
    a('                <posmin x="default" y="default" z="default" />')
    a('                <posmax x="default" y="default" z="default" />')
    a("            </simulationdomain>")
    a("        </parameters>")
    a("    </execution>")
    a("</case>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Particle estimates (measured against GenCase v5.4, see module docstring)
# ---------------------------------------------------------------------------


def _axis_count(length: float, dp: float) -> int:
    return max(1, round(length / dp))


def estimate_particles(p: CaseParams) -> dict[str, Any]:
    """Lattice-arithmetic particle estimate for the dambreak_val2d family.

    Measured rules (GenCase v5.4.354, verified on 8 configurations):
    fluid solid box -> ``round(L/dp)`` per axis; open-top tank shell ->
    ``(round(L/dp)+1) + 2*round(H/dp)``; solid obstacle ->
    ``(round(w/dp)+1)*(round(h/dp)+1)`` with its ``round(w/dp)+1`` floor
    lattice points replacing bottom particles.
    """
    dp = p.dp
    axes = {
        "column_x": _axis_count(p.column_length, dp),
        "column_z": _axis_count(p.column_height, dp),
        "tank_x": _axis_count(p.tank_length, dp),
        "tank_z": _axis_count(p.tank_height, dp),
    }
    fluid = axes["column_x"] * axes["column_z"]
    bound = (axes["tank_x"] + 1) + 2 * axes["tank_z"]
    if p.obstacle is not None:
        axes["obstacle_x"] = _axis_count(p.obstacle.width, dp) + 1
        axes["obstacle_z"] = _axis_count(p.obstacle.height, dp) + 1
        bound += axes["obstacle_x"] * axes["obstacle_z"] - axes["obstacle_x"]
    return {
        "fluid": fluid,
        "bound": bound,
        "total": fluid + bound,
        "axes": axes,
        "note": ESTIMATE_NOTE,
    }


def _summary(p: CaseParams) -> dict[str, Any]:
    (xmin, zmin), (xmax, zmax) = effective_domain(p)
    return {
        "dp": p.dp,
        "pointmin": [xmin, zmin],
        "pointmax": [xmax, zmax],
        "column": [p.column_length, p.column_height],
        "tank": [p.tank_length, p.tank_height],
        "obstacle": None
        if p.obstacle is None
        else [p.obstacle.x, p.obstacle.width, p.obstacle.height],
        "time_max": p.time_max,
        "time_out": p.time_out,
        "gravity": p.gravity,
        "rhop0": p.rhop0,
        "cfl": p.cfl,
        "visco": p.visco,
        "gauges": [
            {
                "name": g.name,
                "type": g.kind,
                "x": g.x,
                "z": g.z,
                "z_top": g.z_top,
                "x_end": g.x_end,
            }
            for g in p.gauges
        ],
        "particle_estimate": estimate_particles(p),
    }


def _obstacle_mk_note(p: CaseParams) -> str | None:
    if p.obstacle is None:
        return None
    return (
        "obstacle uses setmkbound mk=1 -> absolute MKBound 11 in the solver and "
        "post-processing (MeasureTool -onlymk:11, gauge force target mkbound=11)"
    )


# ---------------------------------------------------------------------------
# XML parsing (edit_case / describe_case)
# ---------------------------------------------------------------------------


def _find_child(parent: ET.Element, tag: str, where: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        raise BadInputError(f"missing element {where}")
    return child


def _attr_float(element: ET.Element, attr: str, where: str) -> float:
    raw = element.get(attr)
    if raw is None:
        raise BadInputError(f"missing attribute {where}@{attr}")
    try:
        return float(raw)
    except ValueError as exc:
        raise BadInputError(f"cannot parse {where}@{attr}={raw!r}") from exc


def parse_case_xml(path: Path) -> CaseParams:
    """Parse a ``*_Def.xml`` into CaseParams (dambreak_val2d family only).

    Raises BadInputError when the file is missing, unparseable, 3D, or uses
    geometry outside the supported vocabulary (no fluid drawbox, several
    obstacle boxes, diagonal gauges, ...).
    """
    if not path.is_file():
        raise BadInputError(f"case XML not found: {path}")
    try:
        text = _XML_COMMENT.sub("", path.read_text(encoding="utf-8", errors="replace"))
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise BadInputError(f"cannot parse case XML {path}: {exc}") from exc

    casedef = _find_child(root, "casedef", "case.casedef")
    constants = _find_child(casedef, "constantsdef", "case.casedef.constantsdef")
    gravity_el = _find_child(constants, "gravity", "case.casedef.constantsdef.gravity")
    gravity_z = _attr_float(gravity_el, "z", "gravity")
    if gravity_z >= 0:
        raise BadInputError(
            f"gravity z must be negative (2D XZ plane, z is vertical); got {gravity_z:g}"
        )
    rhop0_el = _find_child(constants, "rhop0", "case.casedef.constantsdef.rhop0")
    rhop0 = _attr_float(rhop0_el, "value", "rhop0")
    cfl_el = constants.find("cflnumber")
    cfl = _attr_float(cfl_el, "value", "cflnumber") if cfl_el is not None else 0.2

    geometry = _find_child(casedef, "geometry", "case.casedef.geometry")
    definition = _find_child(geometry, "definition", "case.casedef.geometry.definition")
    dp = _attr_float(definition, "dp", "definition")
    pointmin = _find_child(definition, "pointmin", "definition.pointmin")
    pointmax = _find_child(definition, "pointmax", "definition.pointmax")
    dmin = (
        _attr_float(pointmin, "x", "pointmin"),
        _attr_float(pointmin, "z", "pointmin"),
    )
    dmax = (
        _attr_float(pointmax, "x", "pointmax"),
        _attr_float(pointmax, "z", "pointmax"),
    )
    if pointmin.get("y") != pointmax.get("y"):
        raise BadInputError(
            "only 2D cases are supported (pointmin.y must equal pointmax.y); "
            f"got y={pointmin.get('y')!r} vs {pointmax.get('y')!r}"
        )

    commands = _find_child(geometry, "commands", "case.casedef.geometry.commands")
    mainlist = _find_child(commands, "mainlist", "...commands.mainlist")
    fluid_box: tuple[float, float, float, float] | None = None
    tank_box: tuple[float, float, float, float] | None = None
    obstacle_boxes: list[tuple[float, float, float, float]] = []
    mode: str | None = None
    bound_mk = 0
    for element in mainlist:
        if element.tag == "setmkfluid":
            mode = "fluid"
        elif element.tag == "setmkbound":
            mode = "bound"
            try:
                bound_mk = int(element.get("mk", "0"))
            except ValueError as exc:
                raise BadInputError(f"cannot parse setmkbound mk={element.get('mk')!r}") from exc
        elif element.tag == "drawbox":
            point = element.find("point")
            size = element.find("size")
            if point is None or size is None:
                raise BadInputError("drawbox without point/size is not supported by the vocabulary")
            box = (
                _attr_float(point, "x", "drawbox.point"),
                _attr_float(point, "z", "drawbox.point"),
                _attr_float(size, "x", "drawbox.size"),
                _attr_float(size, "z", "drawbox.size"),
            )
            if mode == "fluid" and fluid_box is None:
                fluid_box = box
            elif mode == "bound":
                if bound_mk == 0 and tank_box is None:
                    tank_box = box
                elif bound_mk >= 1:
                    obstacle_boxes.append(box)
                else:
                    raise BadInputError("second mk=0 boundary box is not supported")
    if fluid_box is None or tank_box is None:
        raise BadInputError(
            "not the supported dambreak_val2d vocabulary: expected one fluid "
            "drawbox (setmkfluid) and one tank drawbox (setmkbound mk=0)"
        )
    if len(obstacle_boxes) > 1:
        raise BadInputError(
            f"{len(obstacle_boxes)} obstacle boxes found; the vocabulary supports one"
        )
    obstacle = (
        None
        if not obstacle_boxes
        else Obstacle(
            x=obstacle_boxes[0][0], width=obstacle_boxes[0][2], height=obstacle_boxes[0][3]
        )
    )

    visco = 0.02
    time_max: float | None = None
    time_out: float | None = None
    gauges: list[Gauge] = []
    execution = root.find("execution")
    if execution is not None:
        special = execution.find("special")
        if special is not None:
            gauges_el = _find_child(special, "gauges", "execution.special.gauges")
            for swl in gauges_el.findall("swl"):
                name = swl.get("name") or ""
                p0 = _find_child(swl, "point0", "swl.point0")
                p2 = _find_child(swl, "point2", "swl.point2")
                x0 = _attr_float(p0, "x", "swl.point0")
                z0 = _attr_float(p0, "z", "swl.point0")
                x2 = _attr_float(p2, "x", "swl.point2")
                z2 = _attr_float(p2, "z", "swl.point2")
                if x0 == x2:
                    gauges.append(Gauge(kind="vertical", x=x0, name=name or None, z_top=z2))
                elif z0 == z2:
                    gauges.append(Gauge(kind="horizontal", z=z0, name=name or None, x_end=x2))
                else:
                    raise BadInputError(
                        f"swl gauge {name!r} is diagonal; only vertical/horizontal "
                        "probes are supported"
                    )
        parameters = execution.find("parameters")
        if parameters is not None:
            for parameter in parameters.findall("parameter"):
                key = parameter.get("key")
                value = parameter.get("value")
                if key is None or value is None:
                    continue
                try:
                    number = float(value)
                except ValueError:
                    continue
                if key == "TimeMax":
                    time_max = number
                elif key == "TimeOut":
                    time_out = number
                elif key == "Visco":
                    visco = number
    if time_max is None or time_out is None:
        raise BadInputError("missing TimeMax/TimeOut in execution.parameters")

    margins = {
        "x_min": -dmin[0],
        "z_min": -dmin[1],
        "x_max": dmax[0] - tank_box[2],
        "z_max": dmax[1] - tank_box[3],
    }
    domain: tuple[tuple[float, float], tuple[float, float]] | None = None
    if any(m <= 0 for m in margins.values()):
        # Not expressible as tank + positive margins: keep the parsed box
        # verbatim (edit_case validation will then reject it, by design).
        domain = (dmin, dmax)
        margins = {}

    params = CaseParams(
        dp=dp,
        column_length=fluid_box[2],
        column_height=fluid_box[3],
        tank_length=tank_box[2],
        tank_height=tank_box[3],
        gravity=abs(gravity_z),
        rhop0=int(rhop0),
        cfl=cfl,
        visco=visco,
        time_max=time_max,
        time_out=time_out,
        margins=margins,
        domain=domain,
        obstacle=obstacle,
        gauges=tuple(gauges),
    )
    return _autoname(params)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_case(
    out: str,
    template: str = "dambreak_val2d",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a ``*_Def.xml`` from a template plus overrides (no solver)."""
    if template not in TEMPLATES:
        raise BadInputError(f"unknown template {template!r}; available: {sorted(TEMPLATES)}")
    ov = dict(overrides or {})
    validate_overrides_static(ov)  # pure parameter validation first
    params = _build_params(_merge(TEMPLATES[template]["defaults"], ov))
    validate_params(params)

    out_path = Path(out).expanduser().resolve()
    if not out_path.name.endswith(".xml"):
        out_path = out_path.parent / (out_path.name + ".xml")
    if out_path.exists():
        raise BadInputError(
            f"output already exists: {out_path} (use edit_case to modify an existing case)"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_case_xml(params, f"create_case(template={template!r})"))
    return {
        "template": template,
        "out_path": str(out_path),
        "applied": sorted(ov),
        "case": _summary(params),
        "obstacle_mk_note": _obstacle_mk_note(params),
        "next_step": f'gencase(xml_path="{out_path}") to discretise and get real counts',
    }


def edit_case(
    path: str,
    overrides: dict[str, Any] | None = None,
    save_path: str | None = None,
) -> dict[str, Any]:
    """Apply the same override vocabulary to an existing ``*_Def.xml``."""
    ov = dict(overrides or {})
    validate_overrides_static(ov)  # pure parameter validation first (iron rule)

    src = Path(path).expanduser().resolve()
    if not src.name.endswith(".xml"):
        src = src.parent / (src.name + ".xml")
    current = parse_case_xml(src)
    params = _build_params(_merge(_params_to_base(current), ov))
    validate_params(params)

    dest = Path(save_path).expanduser().resolve() if save_path else src
    if not dest.name.endswith(".xml"):
        dest = dest.parent / (dest.name + ".xml")
    if dest != src and dest.exists():
        raise BadInputError(f"save_path already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_case_xml(params, f"edit_case({src.name})"))
    return {
        "path": str(src),
        "saved_to": str(dest),
        "applied": sorted(ov),
        "case": _summary(params),
        "obstacle_mk_note": _obstacle_mk_note(params),
    }


def describe_case(path: str) -> dict[str, Any]:
    """Summarise an existing ``*_Def.xml`` in vocabulary terms (read-only)."""
    src = Path(path).expanduser().resolve()
    if not src.name.endswith(".xml"):
        src = src.parent / (src.name + ".xml")
    params = parse_case_xml(src)
    return {
        "path": str(src),
        "template": "dambreak_val2d",
        "case": _summary(params),
        "obstacle_mk_note": _obstacle_mk_note(params),
    }
