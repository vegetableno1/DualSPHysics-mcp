"""Lightweight tests for examples/render_dambreak.py — no rendering, no solver.

Only the pure helpers (frame discovery / selection / CLI parsing), the
numpy-free paths and the missing-dependency error path run here, so CI stays
green without pyvista, matplotlib or a DualSPHysics toolchain.
"""

from __future__ import annotations

import importlib.util
import sys
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


render = _load_example("render_dambreak")


def _frame_files(numbers: list[int], tmp_path: Path) -> list[Path]:
    files = [tmp_path / f"PartFluid_{n:04d}.vtk" for n in numbers]
    for file in files:
        file.touch()
    return sorted(files, key=render.frame_number)


class TestFrameDiscovery:
    def test_frame_number_parses_trailing_index(self) -> None:
        assert render.frame_number(Path("PartFluid_0007.vtk")) == 7
        assert render.frame_number(Path("particles/PartFluid_0200.vtk")) == 200

    def test_frame_number_rejects_non_frame_files(self) -> None:
        assert render.frame_number(Path("Case_All.vtk")) == -1
        assert render.frame_number(Path("PartFluid_x.vtk")) == -1
        assert render.frame_number(Path("Part_0003.bi4")) == -1

    def test_find_vtk_files_sorts_numerically_and_filters_noise(self, tmp_path: Path) -> None:
        (tmp_path / "Case_All.vtk").touch()
        (tmp_path / "Part_0003.bi4").touch()
        (tmp_path / "PartFluid_0010.vtk").touch()
        (tmp_path / "PartFluid_0002.vtk").touch()
        (tmp_path / "PartFluid_0000.vtk").touch()
        found = render.find_vtk_files(tmp_path)
        assert [render.frame_number(f) for f in found] == [0, 2, 10]

    def test_find_vtk_files_missing_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(render.RenderError, match="not found"):
            render.find_vtk_files(tmp_path / "nope")


class TestFrameSelection:
    def test_every_subsamples_from_the_first_frame(self, tmp_path: Path) -> None:
        files = _frame_files(list(range(6)), tmp_path)
        picked = render.select_frames(files, every=2)
        assert [render.frame_number(f) for f in picked] == [0, 2, 4]

    def test_first_last_clip_by_frame_index(self, tmp_path: Path) -> None:
        files = _frame_files(list(range(10)), tmp_path)
        picked = render.select_frames(files, every=2, first=3, last=7)
        assert [render.frame_number(f) for f in picked] == [3, 5, 7]

    def test_every_below_one_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(render.RenderError, match="--every"):
            render.select_frames(_frame_files([0], tmp_path), every=0)


class TestCli:
    def test_defaults(self) -> None:
        args = render.build_parser().parse_args(["--dirdata", "parts"])
        assert args.dirdata == Path("parts")
        assert args.out == Path("dambreak_animation.mp4")
        assert (args.fps, args.every, args.vars) == (24, 2, "vel")

    def test_explicit_options(self) -> None:
        args = render.build_parser().parse_args(
            [
                "--dirdata",
                "p",
                "--out",
                "a.mp4",
                "--fps",
                "30",
                "--every",
                "3",
                "--vars",
                "rhop",
                "--first",
                "10",
                "--last",
                "90",
                "--tout",
                "0.01",
            ]
        )
        assert (args.fps, args.every, args.vars, args.first, args.last, args.tout) == (
            30,
            3,
            "rhop",
            10,
            90,
            0.01,
        )

    def test_positive_int_rejects_zero(self) -> None:
        with pytest.raises(SystemExit):
            render.build_parser().parse_args(["--dirdata", "p", "--every", "0"])

    def test_main_returns_2_for_bad_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert render.main(["--dirdata", "/nonexistent-dir-xyz", "--out", "x.mp4"]) == 2
        assert "not found" in capsys.readouterr().err


class TestDependencies:
    def test_speed_magnitude(self) -> None:
        numpy = pytest.importorskip("numpy")
        assert list(render.speed_magnitude(numpy.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]))) == [
            pytest.approx(5.0),
            pytest.approx(0.0),
        ]

    def test_read_frame_without_pyvista_gives_install_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "pyvista", None)
        with pytest.raises(render.RenderError, match="pyvista"):
            render.read_frame(Path("PartFluid_0000.vtk"), "vel")
