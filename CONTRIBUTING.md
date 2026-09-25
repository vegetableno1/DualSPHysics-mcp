# Contributing

Thanks for helping improve DualSPHysics MCP. The project is deliberately
small; the fastest path to a merged PR is a focused change that keeps every
check below green.

## Development environment

Python 3.10+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

This creates `.venv` with the package installed editable plus the dev tools
(pytest, ruff, mypy). You do **not** need a DualSPHysics installation to
develop or test — the server only launches the toolchain as subprocesses when
the tools are actually used.

## Tests: the non-negotiable rule

The suite must be **fully green on a machine without any DualSPHysics
installation**. Tests marked `solver` exercise a real local toolchain and
auto-skip when none is found; everything else (tool discovery, command
construction, Run.out/CSV parsing against real log fixtures, validation math,
and a stdio end-to-end test that drives all ten tools) always runs.

To prove you are in the no-solver world — this is exactly the environment CI
runs — force discovery to fail:

```bash
DSPH_TEST_TOOLCHAIN=/nonexistent uv run pytest -q
```

All four checks must pass before you submit:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Bilingual README

`README.md` (English) and `README.zh-CN.md` (Chinese) carry the same content.
Any change to one — new features, badges, reference numbers, examples — must
be mirrored in the other within the same PR.

## Commit style

One concise, imperative subject line per commit ("Add X", "Fix Y"), no ticket
noise. Commits are authored by humans: never append trailers or footer lines
crediting AI tools, bots, or automated assistants of any kind.

## License

By contributing, you agree that your contributions will be licensed under the
MIT License covering this repository (see [LICENSE](LICENSE)).
