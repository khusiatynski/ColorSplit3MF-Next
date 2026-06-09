# Contributing

ColorSplit3MF-Next is experimental research software for No AMS color separation workflows. Contributions should preserve upstream attribution and license information.

## Development Setup

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

## Branches

- `main`: fork baseline and stable project updates
- `dev/noams-mvp`: active MVP development

## Code Guidelines

- Use Python type hints for new code.
- Keep parsing, grouping, export, CLI, and reporting logic separated.
- Prefer deterministic output for filenames and reports.
- Add focused tests for behavior changes.
- Do not remove existing upstream functionality without discussion.

## Test Expectations

Run the test suite before opening a pull request:

```bash
python -m pytest
```

For 3MF parser changes, include at least one synthetic fixture or sample model that demonstrates the behavior.
