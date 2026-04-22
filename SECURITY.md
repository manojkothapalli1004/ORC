# Security Policy

## Supported versions

ORC is pre-1.0. Only the latest `main` branch receives security updates.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please email **manojkothapalli1@gmail.com** with:

- A clear description of the vulnerability
- Steps to reproduce (minimal reproduction preferred)
- Expected vs actual behavior
- Your assessment of potential impact
- Your name or handle if you want to be credited

You should receive an initial acknowledgement within 72 hours. A fix timeline depends on severity and complexity.

## Scope

In scope:
- Authentication / authorization flaws in the LAN access mode
- Any path that could leak `.env`, `data/`, `logs/`, or secret material
- Injection vulnerabilities (SQL, command, path traversal) in API routes
- Known-vulnerable dependencies in `pyproject.toml` / `uv.lock`

Out of scope:
- Social engineering of project maintainers
- Issues in third-party AI provider APIs (report to the provider)
- Running ORC on the public internet (ORC is explicitly local-first)
- Denial of service via excessive local resource consumption

## Handling secrets

ORC is designed so operational secrets never leave the machine:
- API keys live in `.env` (gitignored)
- No telemetry is sent anywhere by default
- The MCP server binds to stdio or 127.0.0.1 only
- The HTTP server defaults to 127.0.0.1; LAN mode requires explicit opt-in plus Basic Auth

If you find any path that violates the above, please report it.
