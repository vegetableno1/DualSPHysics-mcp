#!/usr/bin/env python3
"""Demo: design 2D dam-break experiments with the create_case vocabulary.

Programmatic counterpart of the MCP tools create_case / describe_case /
edit_case (pure Python, no solver needed to DESIGN a case; gencase then
discretises it). Writes two cases into --out:

1. CaseDambreakBaseline_Def.xml — the template defaults, i.e. the official
   CaseDambreakVal2D layout (dp=0.01, 1 m x 2 m column, 4 m x 3 m tank).
2. CaseDambreakObstacle_Def.xml — a variant designed purely with overrides:
   a 1.5 m water column plus a 0.1 m x 0.1 m downstream obstacle at x=2.5.

Measured with GenCase v5.4.354 (matches the lattice estimates exactly):

==========================  ========  ======  ======
case                        fluid     bound   total
==========================  ========  ======  ======
baseline                    20,000    1,001   21,001
variant (column+obstacle)   15,000    1,111   16,111
==========================  ========  ======  ======

Usage:
    python create_case_demo.py [--out DIR]
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any

from dualsphysics_mcp.tools import casegen

VARIANT_OVERRIDES: dict[str, Any] = {
    "column_height": 1.5,
    "obstacle": {"x": 2.5, "width": 0.1, "height": 0.1},
}


def _report(title: str, result: dict[str, Any]) -> None:
    case = result["case"]
    estimate = case["particle_estimate"]
    print(f"\n{title}")
    print(f"  file:              {result['out_path']}")
    print(f"  dp:                {case['dp']} m")
    print(f"  domain:            {case['pointmin']} .. {case['pointmax']} (y pinned to 0)")
    print(f"  water column:      {case['column'][0]} x {case['column'][1]} m")
    print(f"  tank:              {case['tank'][0]} x {case['tank'][1]} m")
    print(f"  obstacle:          {case['obstacle'] or 'none'}")
    print(f"  time:              TimeMax={case['time_max']} s, TimeOut={case['time_out']} s")
    print(f"  gauges:            {[g['name'] for g in case['gauges']]}")
    print(
        f"  particle estimate: fluid={estimate['fluid']:,}"
        f" bound={estimate['bound']:,} total={estimate['total']:,}"
    )
    if result.get("obstacle_mk_note"):
        print(f"  note:              {result['obstacle_mk_note']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: a fresh temp dir, printed and left behind)",
    )
    args = parser.parse_args()
    out_dir = args.out or Path(tempfile.mkdtemp(prefix="dsph_create_case_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    baseline = casegen.create_case(out=str(out_dir / "CaseDambreakBaseline_Def.xml"))
    _report("baseline (official CaseDambreakVal2D layout)", baseline)

    variant = casegen.create_case(
        out=str(out_dir / "CaseDambreakObstacle_Def.xml"), overrides=VARIANT_OVERRIDES
    )
    _report("variant (1.5 m column + downstream obstacle)", variant)

    print("\nnext steps (MCP):")
    print(f'  gencase(xml_path="{variant["out_path"]}")      # real particle counts')
    print('  run_case(case_path="<out>/CaseDambreakObstacle")  # background solve')
    print("\nor edit it further:")
    print(
        "  edit_case(path=..., overrides={'time_max': 3.0, "
        "'gauges': [{'type': 'vertical', 'x': 2.0}]})"
    )


if __name__ == "__main__":
    main()
