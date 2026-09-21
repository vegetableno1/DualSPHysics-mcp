"""Parser for the solver's ``Run.out`` progress log.

The layout differs slightly across DualSPHysics versions; both are accepted:

v5.0/v5.2 rows (6 columns)::

    Part_0001      0.010016           314      314     494.50  21-06-2022 22:48:14

v5.4 rows (8 columns, plain part number; src/source/JSph.cpp ``SaveData``)::

    00001     0.010016         314      314       21001       22720     494.50  21-06-2022 22:48:14

Shared contract (verified against solver source and real logs): the last two
tokens are the solver's projected finish ``date time``, and the numeric token
just before them is the ``Time/Sec`` column — wall-clock seconds per simulated
second for the last part (NOT steps/second). The projected finish time is the
solver's own ETA estimate.

Completion marker (src/source/main.cpp): ``Finished execution (code=N).``
with N==0 on success. Fatal errors surface as ``*** Exception(exc): ...``
lines before a non-zero code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_PART_ROW = re.compile(
    r"^\s*(?:Part_)?(\d{1,5})\s+([0-9]*\.?[0-9]+)\s+([0-9][0-9,]*)\s+([0-9][0-9,]*)\s+(.+?)\s*$"
)
_DATE_TOKEN = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_TIME_TOKEN = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_TIMEMAX_LINE = re.compile(r"^TimeMax=\[?([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)", re.MULTILINE)
_TIMEMAX_BRACKET = re.compile(r"TimeMax=\[([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\]")
_LOADED = re.compile(r"^Loaded particles:\s*([0-9][0-9,]*)", re.MULTILINE)
_CASE_NP = re.compile(r"^CaseNp=([0-9][0-9,]*)", re.MULTILINE)
_CASE_NBOUND = re.compile(r"^CaseNbound=([0-9][0-9,]*)", re.MULTILINE)
_CASE_NFLUID = re.compile(r"^CaseNfluid=([0-9][0-9,]*)", re.MULTILINE)
_FINISHED = re.compile(r"Finished execution \(code=(-?\d+)\)")
_EXCEPTION = re.compile(r"(?im)^\s*\**\s*(?:\*\*\*)?\s*(?:EXCEPTION|Exception|ERR:|Attention).*$")


@dataclass(frozen=True)
class RunProgress:
    """Everything worth reporting from a Run.out snapshot."""

    tmax: float | None
    part: int | None
    part_time: float | None
    total_steps: int | None
    steps_last_part: int | None
    particles: int | None
    cells: int | None
    wall_sec_per_sim_sec: float | None
    solver_eta: str | None
    percent: float | None
    loaded_particles: int | None
    case_np: int | None
    case_nbound: int | None
    case_nfluid: int | None
    finished_code: int | None
    exception_text: str | None

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


_PartRow = tuple[int, float, int, int, int | None, int | None, float, str]


def _parse_part_row(line: str) -> _PartRow | None:
    """Return (part, part_time, total_steps, steps, particles, cells, time_sec, eta)."""
    match = _PART_ROW.match(line)
    if match is None:
        return None
    part = int(match.group(1))
    part_time = float(match.group(2))
    total_steps = _to_int(match.group(3))
    steps = _to_int(match.group(4))
    tokens = match.group(5).split()
    if len(tokens) < 3:
        return None
    # Last two tokens must be the projected finish date + time.
    if not (_DATE_TOKEN.match(tokens[-2]) and _TIME_TOKEN.match(tokens[-1])):
        return None
    numeric = tokens[:-2]
    try:
        time_sec = float(numeric[-1])
        # v5.4 prints particle/cell counters with thousands separators ("21,001").
        mid = [int(token.replace(",", "")) for token in numeric[:-1]]
    except ValueError:
        return None
    particles = mid[-2] if len(mid) >= 2 else None
    cells = mid[-1] if mid else None
    eta = f"{tokens[-2]} {tokens[-1]}"
    return part, part_time, total_steps, steps, particles, cells, time_sec, eta


def parse_run_out(text: str) -> RunProgress:
    """Parse a Run.out snapshot (tolerant: partial/empty logs are fine)."""
    tmax: float | None = None
    match = _TIMEMAX_LINE.search(text) or _TIMEMAX_BRACKET.search(text)
    if match:
        tmax = float(match.group(1))

    part: int | None = None
    part_time: float | None = None
    total_steps: int | None = None
    steps_last: int | None = None
    particles: int | None = None
    cells: int | None = None
    time_sec: float | None = None
    eta: str | None = None

    for line in text.splitlines():
        row = _parse_part_row(line)
        if row is not None:
            part, part_time, total_steps, steps_last, particles, cells, time_sec, eta = row

    percent: float | None = None
    if tmax and part_time is not None and tmax > 0:
        percent = round(part_time / tmax * 100.0, 2)

    finished: int | None = None
    codes = _FINISHED.findall(text)
    if codes:
        finished = int(codes[-1])

    loaded_match = _LOADED.findall(text)
    loaded = _to_int(loaded_match[-1]) if loaded_match else None
    case_np = _to_int(_CASE_NP.findall(text)[-1]) if _CASE_NP.findall(text) else None
    case_nbound = _to_int(_CASE_NBOUND.findall(text)[-1]) if _CASE_NBOUND.findall(text) else None
    case_nfluid = _to_int(_CASE_NFLUID.findall(text)[-1]) if _CASE_NFLUID.findall(text) else None

    exception_text = None
    exc_match = _EXCEPTION.search(text)
    if exc_match:
        exception_text = exc_match.group(0).strip()

    return RunProgress(
        tmax=tmax,
        part=part,
        part_time=part_time,
        total_steps=total_steps,
        steps_last_part=steps_last,
        particles=particles,
        cells=cells,
        wall_sec_per_sim_sec=time_sec,
        solver_eta=eta,
        percent=percent,
        loaded_particles=loaded,
        case_np=case_np,
        case_nbound=case_nbound,
        case_nfluid=case_nfluid,
        finished_code=finished,
        exception_text=exception_text,
    )


def parse_run_out_file(path: Path) -> RunProgress:
    """Parse a Run.out file if it exists; empty progress otherwise."""
    if not path.is_file():
        return parse_run_out("")
    return parse_run_out(path.read_text(encoding="utf-8", errors="replace"))


def _to_int(value: str) -> int:
    """int() that tolerates thousands separators ("21,001" -> 21001)."""
    return int(value.replace(",", ""))
