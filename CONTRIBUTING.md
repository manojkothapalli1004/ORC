# Contributing to ORC

Thanks for your interest in ORC. This project is maintained under the Apache License 2.0 and welcomes contributions.

## Ways to contribute

- Report bugs — open an issue with steps to reproduce, expected vs actual behavior
- Suggest features — open an issue prefixed `[feature]` with a clear use case
- Improve documentation — fix typos, clarify wording, add examples
- Contribute code — see below

## Development setup

Requirements: Python 3.12+ and [`uv`](https://github.com/astral-sh/uv).

```bash
git clone git@github.com:manojkothapalli1004/orc.git
cd orc
uv sync
uv pip install -e .
```

Run the dev server:

```bash
orc start --reload
```

Run the MCP server (for Claude Desktop / Claude Code integration):

```bash
orc mcp
```

## Running tests

```bash
uv run pytest
```

Before opening a PR, please ensure:

- Tests pass locally
- New code includes tests where practical
- Any new runtime config is documented in `.env.example`
- Any user-facing behavior change is noted in `CHANGELOG.md` under an `## Unreleased` heading

## Code style

- Follow existing module structure in `backend/`
- Prefer typed functions and pydantic models for all external interfaces
- Keep routes thin — business logic goes in `backend/services/`
- Local storage goes through `backend/storage/` helpers, not direct file I/O

## Commit messages

- Short, imperative summary (under 72 chars)
- Optional body explaining *why*, not *what*
- Prefix with scope when useful: `cli: add banner`, `storage: filter list_ids`, `docs: update README`

## Pull request checklist

- [ ] Branch is up to date with `main`
- [ ] Tests pass (`uv run pytest`)
- [ ] `CHANGELOG.md` updated under `## Unreleased` if user-facing
- [ ] No secrets, real API keys, or private data staged
- [ ] No commits to `data/`, `logs/`, `.env`, or `bridge/builder_jobs/`

## Security

Do NOT report security issues via public issues. See `SECURITY.md`.

## License

By submitting a contribution, you agree that your contribution will be licensed under the Apache License 2.0 — the same license that covers the rest of the project.
