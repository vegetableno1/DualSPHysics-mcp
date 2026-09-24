"""Tests for the gencase wrapper: command construction, output parsing.

The end-to-end test (real GenCase on the generated example case) is marked
``solver`` and skips when no local toolchain exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dualsphysics_mcp import config
from dualsphysics_mcp.errors import BadInputError, ToolMissingError
from dualsphysics_mcp.tools import gencase
from tests.conftest import LOCAL_TOOLCHAIN, REPO_ROOT

EXAMPLE_XML = REPO_ROOT / "examples" / "dambreak_val2d" / "CaseDambreakVal2D_Def.xml"

GENCASE_STDOUT = """\
GenCase v5.4.354.01 (07-04-2025)
List of available variables: Gravity_z=[-9.81]  MassFluid=[0.1]
Points loaded: 21,001

List of Points:
Points -> Fixed-Boundary particles:
  block[  0] (mk: 10):   1001  -->    1001
Points -> Fluid particles:
  block[  1] (mk:  1):  20000  -->   20000

Particle summary:
  Fixed....: 1,001  id:(0-1000)   MKs:1 (10)
  Fluid....: 20,000  id:(1001-21000)   MKs:1 (1)

Total particles: 21,001 (bound=1001 (fx=1001 mv=0 ft=0) fluid=20000)

Finished execution (code=0).
"""


def test_case_base_name() -> None:
    assert gencase.case_base_name(Path("/tmp/CaseDambreakVal2D_Def.xml")) == "CaseDambreakVal2D"
    assert gencase.case_base_name(Path("/tmp/CaseDambreakVal2D_Def")) == "CaseDambreakVal2D"
    assert gencase.case_base_name(Path("/tmp/CaseX.xml")) == "CaseX"
    assert gencase.case_base_name(Path("/tmp/CaseX")) == "CaseX"


def test_dotted_case_names_survive_suffix_handling(fake_toolchain: Path) -> None:
    """A case base like ``Run2026.1`` must not be mangled to ``Run2026``.

    ``Path.with_suffix`` treats ``.1`` as the suffix; the wrapper's contract is
    string-level: append/strip only a literal ``.xml``.
    """
    exe = fake_toolchain / "GenCase_linux64"
    dotted = Path("/cases/Run2026.1_Def.xml")
    assert gencase.case_base_name(dotted) == "Run2026.1"
    argv = gencase.build_gencase_command(exe, dotted, Path("/out"))
    assert argv[1] == "/cases/Run2026.1_Def"
    assert argv[2] == "/out/Run2026.1"
    # xml lookup / bi4 reporting keep dotted stems too
    assert gencase.append_suffix(gencase.strip_xml_suffix(Path("/out/Run2026.1")), ".bi4") == (
        Path("/out/Run2026.1.bi4")
    )


def test_build_command_strips_def_and_adds_save(fake_toolchain: Path) -> None:
    exe = fake_toolchain / "GenCase_linux64"
    argv = gencase.build_gencase_command(
        exe, Path("/cases/CaseDambreakVal2D_Def.xml"), Path("/cases/CaseDambreakVal2D_out")
    )
    assert argv == [
        str(exe),
        "/cases/CaseDambreakVal2D_Def",
        "/cases/CaseDambreakVal2D_out/CaseDambreakVal2D",
        "-save:all",
    ]
    argv = gencase.build_gencase_command(
        exe,
        Path("/cases/CaseDambreakVal2D_Def.xml"),
        Path("/out"),
        save_modes="",
        extra_args=["-dp:0.02"],
    )
    assert argv[-1] == "-dp:0.02"
    assert "-save:" not in " ".join(argv[3:])


def test_parse_gencase_output_counts() -> None:
    counts = gencase.parse_gencase_output(GENCASE_STDOUT)
    # MassFluid=[0.1] must NOT be picked up as a fluid particle count.
    assert counts["total"] == 21001
    assert counts["fluid"] == 20000
    assert counts["bound"] == 1001


def test_parse_gencase_output_plain_lines() -> None:
    counts = gencase.parse_gencase_output("Total particles: 5300\nfluid particles: 5000\n")
    assert counts["total"] == 5300
    assert counts["fluid"] == 5000
    assert counts["bound"] is None


def test_count_vtk_points_ascii_and_binary(tmp_path: Path) -> None:
    ascii_vtk = tmp_path / "a.vtk"
    ascii_vtk.write_text(
        "# vtk DataFile Version 3.0\nvtk output\nASCII\nDATASET POLYDATA\nPOINTS 21001 float\n"
    )
    assert gencase.count_vtk_points(ascii_vtk) == 21001

    binary_vtk = tmp_path / "b.vtk"
    header = (
        b"# vtk DataFile Version 3.0\nvtk output\nBINARY\nDATASET POLYDATA\nPOINTS 20000 float\n"
    )
    binary_vtk.write_bytes(header + b"\x00" * 32)
    assert gencase.count_vtk_points(binary_vtk) == 20000

    assert gencase.count_vtk_points(tmp_path / "missing.vtk") is None


def test_run_gencase_requires_tool(clean_env: dict[str, str], monkeypatch) -> None:
    config.clear_cache()
    try:
        monkeypatch.setattr(config, "_candidate_dirs", lambda: [])
        monkeypatch.setattr(config, "SCAN_ROOTS", ())
        monkeypatch.delenv("DSPH_GENCASE", raising=False)
        with pytest.raises(ToolMissingError, match="DSPH_TOOL_MISSING"):
            gencase.run_gencase(str(EXAMPLE_XML))
    finally:
        config.clear_cache()


def test_run_gencase_rejects_missing_xml_without_tool(
    clean_env: dict[str, str], monkeypatch
) -> None:
    """Missing case XML is DSPH_BAD_INPUT even with no toolchain installed.

    Guards the validation-before-discovery order (CI machines have no
    DualSPHysics anywhere; a local ~/softwares install must not mask it).
    """
    config.clear_cache()
    try:
        monkeypatch.setattr(config, "_candidate_dirs", lambda: [])
        monkeypatch.setattr(config, "SCAN_ROOTS", ())
        with pytest.raises(BadInputError, match="case XML not found"):
            gencase.run_gencase("/nope/Nothing_Def.xml")
    finally:
        config.clear_cache()


@pytest.mark.solver
def test_gencase_end_to_end_example_case(tmp_path: Path, monkeypatch) -> None:
    """Real GenCase on the generated 2D dam-break case (official counts)."""
    monkeypatch.setenv("DSPH_GENCASE", str(LOCAL_TOOLCHAIN / "GenCase_linux64"))
    config.clear_cache()
    try:
        result = gencase.run_gencase(str(EXAMPLE_XML), output_dir=str(tmp_path / "out"))
    finally:
        config.clear_cache()

    assert result["returncode"] == 0
    assert result["bi4_exists"]
    counts = result["particle_counts"]
    assert counts["total"] == 21001
    assert counts["fluid"] == 20000
    assert counts["bound"] == 1001
