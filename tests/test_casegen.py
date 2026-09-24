"""Tests for casegen: template rendering, override validation, edit/describe.

The particle-estimate anchors in ``test_estimate_matches_measured_gencase``
are real GenCase v5.4.354 counts measured on this machine (see the casegen
module docstring for the lattice rules); the closed loop against a live
GenCase is the ``solver``-marked test at the bottom.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dualsphysics_mcp import config
from dualsphysics_mcp.errors import BadInputError
from dualsphysics_mcp.tools import casegen
from tests.conftest import LOCAL_TOOLCHAIN, REPO_ROOT

EXAMPLE_XML = REPO_ROOT / "examples" / "dambreak_val2d" / "CaseDambreakVal2D_Def.xml"

#: (overrides, measured fluid/bound/total) — all verified against a real
#: GenCase v5.4.354 run on the dambreak_val2d template.
MEASURED_ANCHORS = [
    ({}, (20000, 1001, 21001)),
    ({"column_height": 1.5}, (15000, 1001, 16001)),
    (
        {"column_height": 1.5, "obstacle": {"x": 2.5, "width": 0.1, "height": 0.1}},
        (15000, 1111, 16111),
    ),
    ({"dp": 0.003}, (222111, 3334, 225445)),
    ({"dp": 0.008}, (31250, 1251, 32501)),
    (
        {"column_height": 1.5, "obstacle": {"x": 3.0, "width": 0.2, "height": 0.05}},
        (15000, 1106, 16106),
    ),
    (
        {"column_height": 1.5, "obstacle": {"x": 3.0, "width": 0.33, "height": 0.07}},
        (15000, 1239, 16239),
    ),
    # Sizes that are NOT integer multiples of dp (the estimator's documented
    # weak spot): round() per axis still matches GenCase's lattice fill.
    (
        {
            "column_length": 0.75,
            "column_height": 1.23,
            "tank_length": 3.55,
            "tank_height": 2.7,
            "obstacle": {"x": 2.1, "width": 0.07, "height": 0.13},
        },
        (9225, 1000, 10225),
    ),
]


def _create(tmp_path: Path, name: str = "CaseX", overrides: dict | None = None) -> dict:
    return casegen.create_case(out=str(tmp_path / f"{name}_Def.xml"), overrides=overrides)


def _parse(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Template baseline
# ---------------------------------------------------------------------------


def test_baseline_template_renders_official_layout(tmp_path: Path) -> None:
    result = _create(tmp_path, "CaseBaseline")
    path = Path(result["out_path"])
    assert path.name == "CaseBaseline_Def.xml"
    assert path.is_file()

    root = _parse(path)
    definition = root.find("casedef/geometry/definition")
    assert definition is not None and definition.get("dp") == "0.01"
    pointmin = definition.find("pointmin")
    pointmax = definition.find("pointmax")
    assert pointmin is not None and pointmax is not None
    # 2D convention: y planes pinned to 0 on both domain corners.
    assert pointmin.get("y") == pointmax.get("y") == "0"
    assert (pointmin.get("x"), pointmin.get("z")) == ("-1", "-1")
    assert (pointmax.get("x"), pointmax.get("z")) == ("4.5", "3.5")

    mainlist = root.find("casedef/geometry/commands/mainlist")
    assert mainlist is not None
    tags = [child.tag for child in mainlist]
    # drawmode full is mandatory (without it the fluid loses a lattice row).
    drawmode = mainlist.find("setdrawmode")
    assert tags[0] == "setdrawmode" and drawmode is not None and drawmode.get("mode") == "full"
    assert tags[:3] == ["setdrawmode", "setmkfluid", "drawbox"]

    fluid_box, tank_box = mainlist.findall("drawbox")[:2]
    assert fluid_box.find("boxfill").text == "solid"  # type: ignore[union-attr]
    fsize = fluid_box.find("size")
    tsize = tank_box.find("size")
    assert fsize is not None and (fsize.get("x"), fsize.get("z")) == ("1", "2")
    assert tsize is not None and (tsize.get("x"), tsize.get("z")) == ("4", "3")
    assert tank_box.find("boxfill").text == "bottom | left | right | front | back"  # type: ignore[union-attr]
    # 2D XZ convention: every box spans y in [-1, 1] so it crosses y = 0.
    for box in (fluid_box, tank_box):
        point = box.find("point")
        size = box.find("size")
        assert point is not None and size is not None
        assert (point.get("y"), size.get("y")) == ("-1", "2")

    parameters = {
        p.get("key"): p.get("value") for p in root.findall("execution/parameters/parameter")
    }
    assert parameters["TimeMax"] == "2" and parameters["TimeOut"] == "0.01"

    gauges = root.findall("execution/special/gauges/swl")
    assert [g.get("name") for g in gauges] == ["Swl_x02", "Swl_z003"]

    estimate = result["case"]["particle_estimate"]
    assert (estimate["fluid"], estimate["bound"], estimate["total"]) == (20000, 1001, 21001)
    assert result["obstacle_mk_note"] is None
    assert "gencase" in result["next_step"]


def test_generated_baseline_matches_official_example_semantics(tmp_path: Path) -> None:
    """create_case() with no overrides == the shipped validation case.

    Compares the parsed vocabulary (not bytes) against
    examples/dambreak_val2d/CaseDambreakVal2D_Def.xml.
    """
    created = casegen.describe_case(_create(tmp_path, "CaseBaseline")["out_path"])
    official = casegen.describe_case(str(EXAMPLE_XML))
    assert created["case"] == official["case"]


# ---------------------------------------------------------------------------
# Particle estimates vs measured GenCase counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overrides,expected", MEASURED_ANCHORS)
def test_estimate_matches_measured_gencase(
    tmp_path: Path, overrides: dict, expected: tuple[int, int, int]
) -> None:
    result = _create(tmp_path, "Anchors", overrides)
    estimate = result["case"]["particle_estimate"]
    assert (estimate["fluid"], estimate["bound"], estimate["total"]) == expected


def test_estimate_note_marks_it_as_estimate(tmp_path: Path) -> None:
    result = _create(tmp_path)
    assert "authoritative" in result["case"]["particle_estimate"]["note"]


# ---------------------------------------------------------------------------
# Override validation (pure parameters first)
# ---------------------------------------------------------------------------


def test_unknown_template_rejected(tmp_path: Path) -> None:
    with pytest.raises(BadInputError, match="unknown template"):
        casegen.create_case(out=str(tmp_path / "x.xml"), template="wavemaker")


def test_unknown_override_key_lists_vocabulary(tmp_path: Path) -> None:
    with pytest.raises(BadInputError, match=r"unknown override key.*obstacle_x"):
        _create(tmp_path, overrides={"obstacle_x": 2.5})


def test_static_validation_precedes_filesystem(tmp_path: Path) -> None:
    """Bad numbers are DSPH_BAD_INPUT before any path is touched (iron rule)."""
    with pytest.raises(BadInputError, match="'dp' must be > 0"):
        casegen.create_case(out="/nonexistent/deep/case.xml", overrides={"dp": -1})
    with pytest.raises(BadInputError, match="unknown override key"):
        casegen.create_case(out="/nonexistent/deep/case.xml", overrides={"nope": 1})
    with pytest.raises(BadInputError, match="'dp' must be > 0"):
        casegen.edit_case("/nonexistent/X_Def.xml", {"dp": -1})
    # ... while a missing file with VALID overrides still reports the file.
    with pytest.raises(BadInputError, match="case XML not found"):
        casegen.edit_case("/nonexistent/X_Def.xml", {"dp": 0.02})


def test_domain_override_must_contain_geometry(tmp_path: Path) -> None:
    """The anti silent-clipping guard: GenCase would exit 0 and clip."""
    with pytest.raises(BadInputError, match="silently clipped"):
        _create(
            tmp_path,
            overrides={"domain": {"pointmin": [-1, -1], "pointmax": [2, 3]}},
        )
    with pytest.raises(BadInputError, match="pointmin x=0"):
        _create(
            tmp_path,
            overrides={"domain": {"pointmin": [0, -1], "pointmax": [4.5, 3.5]}},
        )
    # Equality on the max side is also rejected: a domain boundary flush
    # with the tank clips the boundary lattice row (measured GenCase rule).
    with pytest.raises(BadInputError, match=r"pointmax x=4 must be > tank_length"):
        _create(
            tmp_path,
            overrides={"domain": {"pointmin": [-1, -1], "pointmax": [4, 3.5]}},
        )


def test_nonpositive_margin_rejected(tmp_path: Path) -> None:
    with pytest.raises(BadInputError, match="margins 'x_max' must be > 0"):
        _create(tmp_path, overrides={"margins": {"x_max": 0}})


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"dp": 0}, "'dp' must be > 0"),
        ({"column_height": 5}, "column_height.*must be <= tank_height"),
        ({"column_length": 5}, "column_length.*must be <= tank_length"),
        ({"time_out": 3.0}, "time_out.*must be in"),
        ({"cfl": 1.5}, "'cfl' must be in \\(0, 1]"),
        ({"visco": -0.1}, "'visco' must be >= 0"),
        ({"column_height": 0.001}, "at least dp"),
        (
            {"obstacle": {"x": 0.5, "width": 0.1, "height": 0.1}},
            "must be >= column_length",
        ),
        (
            {"obstacle": {"x": 3.95, "width": 0.1, "height": 0.1}},
            "exceeds the tank",
        ),
        ({"obstacle": {"x": 2.5, "width": 0.1}}, "obstacle requires 'height'"),
        ({"obstacle": 3}, "must be an object or null"),
        ({"gauges": [{"type": "diagonal"}]}, "type must be one of"),
        ({"gauges": [{"type": "vertical"}]}, "vertical gauge requires 'x'"),
        ({"gauges": [{"type": "vertical", "x": 9.0}]}, "must be within.*tank_length"),
        ({"gauges": [{"type": "horizontal", "z": 9.0}]}, "must be within.*tank_height"),
        ({"gauges": [{"type": "vertical", "x": 0.2, "z_top": 1.0}]}, "above the column height"),
        (
            {
                "gauges": [
                    {"type": "vertical", "x": 0.2, "name": "G1"},
                    {"type": "vertical", "x": 0.3, "name": "G1"},
                ]
            },
            "duplicate gauge name",
        ),
        ({"gauges": "x=0.2"}, "must be a list"),
    ],
)
def test_invalid_overrides_rejected(tmp_path: Path, overrides: dict, match: str) -> None:
    with pytest.raises(BadInputError, match=match):
        _create(tmp_path, overrides=overrides)


def test_variant_with_obstacle_and_gauges(tmp_path: Path) -> None:
    result = _create(
        tmp_path,
        "CaseVariant",
        overrides={
            "column_height": 1.5,
            "obstacle": {"x": 2.5, "width": 0.1, "height": 0.1},
            "gauges": [
                {"type": "vertical", "x": 0.2},
                {"type": "horizontal", "z": 0.03},
            ],
        },
    )
    estimate = result["case"]["particle_estimate"]
    assert (estimate["fluid"], estimate["bound"], estimate["total"]) == (15000, 1111, 16111)
    assert result["obstacle_mk_note"] is not None and "MKBound 11" in result["obstacle_mk_note"]

    mainlist = _parse(Path(result["out_path"])).find("casedef/geometry/commands/mainlist")
    assert mainlist is not None
    mks = [child.get("mk") for child in mainlist if child.tag == "setmkbound"]
    assert mks == ["0", "1"]
    obstacle_box = mainlist.findall("drawbox")[2]
    point, size = obstacle_box.find("point"), obstacle_box.find("size")
    assert point is not None and size is not None
    assert (point.get("x"), size.get("x"), size.get("z")) == ("2.5", "0.1", "0.1")
    assert obstacle_box.find("boxfill").text == "solid"  # type: ignore[union-attr]

    # gauge z_top auto-sizes above the new column height
    vertical = result["case"]["gauges"][0]
    assert vertical["z_top"] == pytest.approx(1.6)
    assert result["case"]["obstacle"] == [2.5, 0.1, 0.1]
    # removing the obstacle again restores the baseline bound estimate
    edited = casegen.edit_case(
        result["out_path"], {"obstacle": None}, save_path=str(tmp_path / "CaseNoObst_Def.xml")
    )
    assert edited["case"]["particle_estimate"]["bound"] == 1001


def test_custom_gauges_autonamed_and_suffixed(tmp_path: Path) -> None:
    result = _create(
        tmp_path,
        overrides={"gauges": [{"type": "vertical", "x": 0.3}, {"type": "vertical", "x": 0.3}]},
    )
    names = [g["name"] for g in result["case"]["gauges"]]
    assert names[0].startswith("Swl_x0p3") and names[0] != names[1]


def test_create_refuses_existing_output(tmp_path: Path) -> None:
    first = _create(tmp_path, "CaseOnce")
    with pytest.raises(BadInputError, match="already exists"):
        casegen.create_case(out=first["out_path"])


def test_out_suffix_appended_and_applied_sorted(tmp_path: Path) -> None:
    result = casegen.create_case(
        out=str(tmp_path / "plain"), overrides={"time_max": 3.0, "dp": 0.02}
    )
    assert result["out_path"].endswith("plain.xml")
    assert result["applied"] == ["dp", "time_max"]


# ---------------------------------------------------------------------------
# describe / edit round trips
# ---------------------------------------------------------------------------


def test_edit_round_trip_updates_and_summarises(tmp_path: Path) -> None:
    created = _create(tmp_path, "CaseEdit")
    edited = casegen.edit_case(
        created["out_path"],
        {"column_height": 1.5, "obstacle": {"x": 2.5, "width": 0.1, "height": 0.1}},
    )
    assert edited["applied"] == ["column_height", "obstacle"]
    assert edited["saved_to"] == created["out_path"]
    estimate = edited["case"]["particle_estimate"]
    assert (estimate["fluid"], estimate["bound"], estimate["total"]) == (15000, 1111, 16111)

    described = casegen.describe_case(created["out_path"])
    assert described["case"] == edited["case"]
    assert described["case"]["column"] == [1.0, 1.5]


def test_edit_save_path_leaves_original_untouched(tmp_path: Path) -> None:
    created = _create(tmp_path, "CaseOrig")
    original = Path(created["out_path"]).read_text(encoding="utf-8")
    saved = casegen.edit_case(
        created["out_path"], {"time_max": 5.0}, save_path=str(tmp_path / "CaseNew_Def.xml")
    )
    assert Path(created["out_path"]).read_text(encoding="utf-8") == original
    assert saved["case"]["time_max"] == 5.0
    assert casegen.describe_case(created["out_path"])["case"]["time_max"] == 2.0
    with pytest.raises(BadInputError, match="save_path already exists"):
        casegen.edit_case(
            created["out_path"], {"time_max": 4.0}, save_path=str(tmp_path / "CaseNew_Def.xml")
        )


def test_describe_official_example_case() -> None:
    result = casegen.describe_case(str(EXAMPLE_XML))
    case = result["case"]
    assert case["dp"] == 0.01
    assert case["pointmin"] == [-1.0, -1.0] and case["pointmax"] == [4.5, 3.5]
    assert case["column"] == [1.0, 2.0] and case["tank"] == [4.0, 3.0]
    assert case["obstacle"] is None
    assert (case["time_max"], case["time_out"]) == (2.0, 0.01)
    assert [(g["name"], g["type"]) for g in case["gauges"]] == [
        ("Swl_x02", "vertical"),
        ("Swl_z003", "horizontal"),
    ]
    assert case["gauges"][0]["z_top"] == pytest.approx(2.1)
    assert case["gauges"][1]["x_end"] == pytest.approx(4.05)
    estimate = case["particle_estimate"]
    assert (estimate["fluid"], estimate["bound"], estimate["total"]) == (20000, 1001, 21001)


def test_edit_official_example_changes_dp(tmp_path: Path) -> None:
    edited = casegen.edit_case(
        str(EXAMPLE_XML), {"dp": 0.02}, save_path=str(tmp_path / "Coarse_Def.xml")
    )
    estimate = edited["case"]["particle_estimate"]
    assert edited["case"]["dp"] == 0.02
    assert (estimate["fluid"], estimate["bound"], estimate["total"]) == (5000, 501, 5501)


def test_parse_rejects_non_family_xml(tmp_path: Path) -> None:
    empty = tmp_path / "Empty_Def.xml"
    empty.write_text("<case/>", encoding="utf-8")
    with pytest.raises(BadInputError, match=r"dambreak_val2d vocabulary|missing element"):
        casegen.describe_case(str(empty))

    broken = tmp_path / "Broken_Def.xml"
    broken.write_text("<case><casedef>", encoding="utf-8")
    with pytest.raises(BadInputError, match="cannot parse"):
        casegen.describe_case(str(broken))

    # 3D domain (pointmin.y != pointmax.y) is outside the vocabulary.
    three_d = tmp_path / "ThreeD_Def.xml"
    three_d.write_text(
        "<case><casedef>"
        '<constantsdef><gravity x="0" y="0" z="-9.81" /><rhop0 value="1000" /></constantsdef>'
        '<geometry><definition dp="0.01">'
        '<pointmin x="0" y="0" z="0" /><pointmax x="1" y="1" z="1" />'
        "</definition></geometry></casedef></case>",
        encoding="utf-8",
    )
    with pytest.raises(BadInputError, match="only 2D cases are supported"):
        casegen.describe_case(str(three_d))


def test_parse_tolerates_double_hyphen_comments(tmp_path: Path) -> None:
    """DualSPHysics files often put "--" inside comments (illegal XML)."""
    created = _create(tmp_path, "CaseComment")
    path = Path(created["out_path"])
    text = path.read_text(encoding="utf-8").replace("Generated by", "Generated -- by", 1)
    path.write_text(text, encoding="utf-8")
    described = casegen.describe_case(str(path))
    assert described["case"]["particle_estimate"]["total"] == 21001


# ---------------------------------------------------------------------------
# Closed loop with a real GenCase (auto-skipped without a toolchain)
# ---------------------------------------------------------------------------


@pytest.mark.solver
@pytest.mark.parametrize(
    "overrides,expected",
    [({}, (20000, 1001, 21001)), (MEASURED_ANCHORS[2][0], MEASURED_ANCHORS[2][1])],
)
def test_create_gencase_closed_loop(
    tmp_path: Path, monkeypatch, overrides: dict, expected: tuple[int, int, int]
) -> None:
    """create_case -> real gencase: counts must equal the estimates."""
    from dualsphysics_mcp.tools import gencase

    monkeypatch.setenv("DSPH_GENCASE", str(LOCAL_TOOLCHAIN / "GenCase_linux64"))
    config.clear_cache()
    try:
        created = casegen.create_case(out=str(tmp_path / "CaseLoop_Def.xml"), overrides=overrides)
        estimate = created["case"]["particle_estimate"]
        run = gencase.run_gencase(created["out_path"], output_dir=str(tmp_path / "out"))
    finally:
        config.clear_cache()

    counts = run["particle_counts"]
    assert run["returncode"] == 0 and run["bi4_exists"]
    assert (counts["fluid"], counts["bound"], counts["total"]) == expected
    assert expected == (
        estimate["fluid"],
        estimate["bound"],
        estimate["total"],
    )
