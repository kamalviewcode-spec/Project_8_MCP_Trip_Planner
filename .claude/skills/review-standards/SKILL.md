---
name: review-standards
description: Review code changes against standard quality criteria (correctness, error handling, security, readability) plus this project's MCP/LangGraph conventions. Use when asked to review code, a diff, or a file for quality issues.
---

# Code Review — Standard Instructions

Review the code the user points at (a file, a diff, or the current working-tree changes via `git diff`). If nothing is specified, review the uncommitted changes.

## How to review

1. Read the target code fully before commenting — never review from the diff alone if surrounding context matters.
2. Rank findings by severity: **Critical** (bugs, data loss, security) → **Major** (error handling, correctness edge cases) → **Minor** (readability, naming, style).
3. For each finding give: `file:line`, what's wrong, why it matters, and a concrete suggested fix.
4. Do NOT modify any files — this is a read-only review. Report findings only.
5. If the code is fine, say so plainly. Do not invent nitpicks to fill space.

## Standard criteria

### Correctness
- Logic errors, off-by-one, wrong operator, inverted conditions.
- Unhandled `None`/empty/missing-key cases on external data (API responses, env vars, user input).
- Mutable default arguments, shadowed variables, unreachable code.

### Error handling
- Bare `except:` or overly broad `except Exception` that swallows real failures silently.
- External calls (HTTP, DB, MCP tools, LLM calls) without timeout or failure handling.
- Errors surfaced to the user vs. silently logged — flag silent failures in user-facing flows.

### Security
- Secrets or API keys hardcoded instead of read from `.env`.
- SQL built by string concatenation instead of parameterized queries.
- Unvalidated user input passed into file paths, shell commands, or URLs.

### Readability & maintainability
- Functions doing too many things; duplicated logic that should be shared.
- Dead code, stale comments, commented-out blocks left behind.
- Names that lie about what the thing does.

### Performance
- Repeated expensive calls (LLM, network, DB) inside loops that could be batched or cached.
- Loading/parsing the same resource multiple times per request.

## Project-specific checks (this repo)

- **Sync/async boundary**: agents in `Src/planner.py` are sync and call `asyncio.run(...)` per MCP call. Flag any code that calls agent functions from inside a running event loop, or that nests `asyncio.run`.
- **Destination extraction**: any new agent doing a search must search on `extract_destination()` output, not the raw user query (origin-city pollution bug).
- **MCP result parsing**: MCP results are content-block envelopes, not strings. Flag raw parsing in agents — must go through `_mcp_result_to_raw_text` / `_format_*` helpers in `mcp_client.py`.
- **Import-time side effects**: `Src/planner.py` connects to Postgres and `mcp_client.py` calls `get_llm()` at import time. Flag any *new* module-level side effects being added (network calls, DB connects, subprocess spawns).
- **State conventions**: `TravelState.messages` uses an `operator.add` reducer; all other fields are last-write-wins. Flag agents that append to non-reducer fields expecting accumulation.
- **Dead code**: `custom_mcp_aviation_server.py` is dead — flag any new imports of it.

## Output format

```
## Review: <target>

### Critical
- `file.py:42` — <issue>. <why>. Fix: <suggestion>

### Major
...

### Minor
...

**Verdict:** <ready / needs fixes / needs discussion>
```
