# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-agent AI trip planner: one free-text travel request runs through a fixed LangGraph pipeline of 4 agents (flight → hotel → weather → itinerary), each fetching live data through MCP servers. `Guide.md` is a detailed file-by-file walkthrough — read it for depth beyond this summary.

## Commands

```bash
# Activate the venv first (Windows)
venv\Scripts\activate

# CLI entry point (prompts for a travel request in the terminal)
python Src/planner.py

# Gradio web UI (same pipeline, streams results tab-by-tab)
python frontend.py

# Switch LLM provider for a run (default: groq)
set LLM_PROVIDER=claude   # or gemini / openai / deepseek / groq

# Install deps
pip install -e .
```

There is no test suite or linter in the main project (`Testing/` is empty). The vendored `aviationstack-mcp/` sub-repo has its own tests under `aviationstack-mcp/tests/`.

## Runtime prerequisites (things break without these)

- **PostgreSQL must be running** and `DATABASE_URL` set — `Src/planner.py` connects at **import time** (module-level `psycopg.connect` + `PostgresSaver.setup()`). Merely importing `planner` fails without a live DB.
- `mcp_client.py` also calls `get_llm()` at module level, so importing it requires a valid API key for the selected provider in `.env`.
- The AviationStack MCP server is a **separately cloned repo** (`aviationstack-mcp/`, from `pradumnasaraf/aviationstack-mcp`) with its **own `.venv`**. `mcp_client.py` hardcodes the path `aviationstack-mcp/.venv/Scripts/python.exe` — if that venv doesn't exist, the stdio server can't launch. It is intentionally kept in a separate venv to avoid dependency conflicts; don't merge it into the main venv.
- Required `.env` keys: `DATABASE_URL`, `TAVILY_API_KEY`, `AVIATION_STACK_API_KEY`, `OPENWEATHER_API_KEY`, plus the API key for whichever `LLM_PROVIDER` is active (`GROQ_API_KEY` by default).

## Architecture

Data flow: `frontend.py` (Gradio UI) → `Src/planner.py` (LangGraph pipeline + agents) → `mcp_client.py` (MCP bridge + LLM selection) → 3 MCP servers.

- **`Src/planner.py`** — defines `TravelState` (shared state dict; `messages` uses an `operator.add` reducer, all other fields are last-write-wins), the 4 agent functions, and the straight-line graph (no branching). The compiled `app` is checkpointed to Postgres per `thread_id` via `PostgresSaver`. The Postgres connection uses `autocommit=True` because `checkpointer.setup()` runs `CREATE INDEX CONCURRENTLY`, which Postgres refuses inside a transaction.
- **`mcp_client.py`** — configures `MultiServerMCPClient` with 3 servers over 2 transports: Tavily (hosted, `streamable_http`), aviationstack and weather (local subprocesses, `stdio`). Exposes clean async wrappers (`tavily_mcp_search`, `weather_mcp_search`, `forecast_mcp_search`, `get_airports`, `get_airlines`, `aviation_mcp_call`) plus `get_llm()` (provider switch on `LLM_PROVIDER`) and `extract_destination()` (LLM-based, see gotcha below). Tool handles are discovered once and cached (`initialize_mcp`) since `client.get_tools()` spawns/queries every server.
- **`frontend.py`** — pure UI layer; imports the same compiled `app` from `planner.py` (no duplicated agent logic). `plan_trip()` is a generator using `travel_app.stream(..., stream_mode="values")` so tabs fill in progressively as each agent finishes. Generates `.txt` and `.pdf` (reportlab) downloads of the itinerary.
- **`custom_mcp_weather_server.py`** — local FastMCP stdio server (`get_current_weather`, `get_forecast`) backed by OpenWeatherMap; launched as a subprocess of the main venv by `mcp_client.py`, never run standalone.

## Gotchas / conventions

- **`custom_mcp_aviation_server.py` is dead code** — an earlier unused attempt, superseded by the cloned `aviationstack-mcp` repo. Ignore it.
- **Agents are sync, MCP client is async**: each agent calls `asyncio.run(...)` per MCP call, opening a fresh event loop each time. Don't call these agent functions from inside an already-running event loop.
- **Search on the extracted destination, not the raw query**: `extract_destination()` exists because user queries often mention the origin city too ("from Des Moines to Bali"), and searching hotels/weather on the whole string returned origin-city results — a real bug fixed during development. Preserve this pattern in any new agent.
- `flight_agent` deliberately does not call a real flight-pricing API — it feeds airport/airline reference data to the LLM and asks it to reason about routes/fares, so the demo works without a paid flight-search key.
- MCP tool results arrive as content-block envelopes (`[{"type": "text", "text": ...}]`), not plain strings — use/extend the `_mcp_result_to_raw_text` / `_format_*` helpers in `mcp_client.py` rather than parsing raw results in agents.
- Path wiring is manual: `Src/planner.py` inserts the project root into `sys.path` to import `mcp_client`; `frontend.py` inserts `Src/` to import `planner`.
- `mcp_client.py` has a stale `if __name__ == "__main__": asyncio.run(main())` block referencing a commented-out `main()` — running it directly fails; it's meant to be imported.
