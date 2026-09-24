---
name: Bug report
about: A tool behaves incorrectly, errors out, or returns wrong numbers
title: ''
labels: bug
assignees: ''
---

**Description**

A clear and concise description of the bug.

**To reproduce**

1. Tool called: `check_environment` / `gencase` / `run_case` / `job_status` / `partvtk` / `measure_tool` / `validate_dambreak`
2. Arguments passed: `...`
3. What happened instead: `...`

**Expected behavior**

What you expected to happen.

**Environment**

- OS:
- Python version:
- dualsphysics-mcp version (`uv pip show dualsphysics-mcp`, or git revision):
- MCP client name and version (desktop app / IDE / other):
- DualSPHysics version and install path (if installed):
- Relevant `DSPH_*` environment variables (paths only, no secrets):

**Logs / output**

```
Paste the tool output, job_status Run.out tail, or stack trace here.
```

**Points file / case XML (if relevant)**

If the bug involves `measure_tool` or `validate_dambreak`, attach or inline
the points file; if it involves `gencase` / `run_case`, describe the case XML.
