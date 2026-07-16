# Project Guide: AI Trip Planner

A multi-agent travel planner built with **LangGraph** (agent orchestration), **MCP** (Model Context
Protocol — how agents fetch live data), **Groq/Llama** (the LLM), **Postgres** (conversation memory),
and **Gradio** (the web UI).

This guide explains how the pieces fit together and in what order to read the code.

---

## 1. The big picture

One user request ("Plan a 5-day trip to Bali") flows through four agents in a fixed pipeline:

```
START
  │
  ▼
flight_agent   → reasons about likely flights using live airport/airline data
  │
  ▼
hotel_agent    → searches the web for hotels at the destination
  │
  ▼
weather_agent  → fetches current weather + forecast for the destination
  │
  ▼
itinerary_agent → LLM combines everything above into a final day-by-day plan
  │
  ▼
 END
```

Each agent is just a Python function that reads a shared state dict, does some work
(calling an external data source and/or the LLM), and returns a partial update to that
state. LangGraph wires these functions into a graph and runs them in order.

The "live data" each agent needs (airports, hotels, weather) doesn't come from hardcoded
API calls — it comes through **MCP servers**, each one a small standalone program that
exposes a couple of "tools" (functions) an agent can call. This is what makes the project
an MCP demo, not just a LangGraph demo.

---

## 2. Read the files in this order

| Order | File | What it is |
|---|---|---|
| 1 | `custom_mcp_weather_server.py` | Simplest MCP server — good starting point to understand what an MCP server actually is |
| 2 | `aviationstack-mcp/src/aviationstack_mcp/server.py` | A second, more complex MCP server (third-party, cloned as its own sub-project) |
| 3 | `mcp_client.py` | Connects to all 3 MCP servers and exposes clean Python functions the agents call |
| 4 | `Src/planner.py` | Defines the 4 agents and wires them into the LangGraph pipeline; the CLI entry point |
| 5 | `frontend.py` | Gradio web UI wrapped around the same compiled graph from `planner.py` |

---

## 3. `custom_mcp_weather_server.py` — what an MCP server looks like

```python
mcp = FastMCP("Weather Server")

@mcp.tool()
def get_current_weather(city: str):
    ...

@mcp.tool()
def get_forecast(city: str):
    ...

mcp.run()
```

- `FastMCP` (from the `mcp` package) turns plain Python functions into MCP "tools" —
  callable over a standard protocol, the same way a REST endpoint is callable over HTTP.
- `mcp.run()` starts the server listening on **stdio** (its own process's stdin/stdout) —
  there's no network port. Whatever launches this script talks to it by writing/reading
  JSON-RPC messages over that process's pipes.
- This server is launched as a **subprocess** by `mcp_client.py`, not run standalone.

---

## 4. `aviationstack-mcp/` — a second MCP server, but external

This is a **separate cloned GitHub repo** (`pradumnasaraf/aviationstack-mcp`), living inside
this project with its own `pyproject.toml`, its own `.venv/`, and its own `.git/`. It exposes
tools like `list_airports`, `list_airlines`, `flights_with_airline`, etc., backed by the real
AviationStack REST API.

It's kept separate (own virtualenv) because it's third-party code with its own dependency
versions — mixing it into this project's `venv` risked version conflicts.

> Note: `custom_mcp_aviation_server.py` in the project root is an **earlier, unused
> attempt** at a homemade aviation server (static dataset, no API key needed) — it was
> superseded once the real `aviationstack-mcp` repo was found already present on disk.
> It's currently dead code; safe to ignore or delete.

---

## 5. `mcp_client.py` — the bridge between agents and MCP servers

This is the most important file to understand MCP itself. Key ideas:

**`MultiServerMCPClient`** (from `langchain_mcp_adapters`) is configured with 3 servers:

```python
client = MultiServerMCPClient({
    "tavily":        {"transport": "streamable_http", "url": ...},   # remote, over HTTP
    "aviationstack":  {"transport": "stdio", "command": <aviationstack venv's python>, ...},
    "weather":        {"transport": "stdio", "command": sys.executable, ...},
})
```

Two transport types are used:
- **`streamable_http`** — talks to Tavily's officially hosted MCP server over the internet.
- **`stdio`** — launches a local script as a subprocess (aviationstack + weather servers
  both run locally on this machine).

**Tool discovery** (`initialize_mcp()` / `initialize_weather_tools()`): before you can call
a tool, you have to ask each server "what tools do you have?" (`client.get_tools()`). This
is done once and cached, rather than on every single search, since spawning/querying every
server repeatedly would be slow.

**Result formatting**: MCP tool results don't come back as plain strings — they come back
as a list of "content blocks", e.g. `[{"type": "text", "text": "...json..."}]`. Several
helper functions (`_mcp_result_to_raw_text`, `_format_tavily_result`,
`_format_current_weather`, `_format_forecast`) unwrap that envelope and turn the underlying
JSON into clean, readable text — both for display in the UI and so the LLM isn't fed a raw
JSON blob when generating the itinerary.

**`extract_destination(query)`**: uses the LLM (not regex) to pull just the destination
city out of a query like *"Travelling from Des Moines to Bali for 7 days"*. This matters
because the raw query mentions the origin too — searching hotels/weather on the whole
string was pulling up the wrong city (a real bug that was fixed during development).

Public functions this file exposes (these are what `planner.py` imports):

| Function | Backed by |
|---|---|
| `tavily_mcp_search(query)` | Tavily MCP server (hotel/general web search) |
| `get_airports()` / `get_airlines()` | aviationstack MCP server |
| `aviation_mcp_call(tool_name, **args)` | generic call to any aviationstack tool |
| `weather_mcp_search(city)` / `forecast_mcp_search(city)` | weather MCP server |
| `extract_destination(query)` | LLM directly (no MCP) |

---

## 6. `Src/planner.py` — the LangGraph pipeline

**State** (`TravelState`): the dict that flows through every node.

```python
class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]   # append-only log
    user_query: str
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str
    llm_calls: int
```

`messages` uses `operator.add` as its "reducer" — new messages get appended to the list.
Every other field is plain last-write-wins.

**The 4 agent functions**, each a plain sync function that calls `asyncio.run(...)` to
invoke the async MCP client functions from `mcp_client.py`:

- `flight_agent` — doesn't call a real flight-pricing API. It hands the LLM live
  airport/airline lists and asks it to reason about a plausible route/price range. This
  keeps the demo working without needing a paid flight-search API key.
- `hotel_agent` — extracts the destination first, then searches `"Best hotels in
  {destination}"` via Tavily.
- `weather_agent` — same destination-extraction approach, fetches current weather + a
  5-step forecast.
- `itinerary_agent` — the final LLM call: takes everything the previous 3 agents produced
  and writes the actual day-by-day itinerary.

**Graph wiring** — a straight-line pipeline, no branching:

```python
graph.add_edge(START, "flight_agent")
graph.add_edge("flight_agent", "hotel_agent")
graph.add_edge("hotel_agent", "weather_agent")
graph.add_edge("weather_agent", "itinerary_agent")
graph.add_edge("itinerary_agent", END)
```

**Persistence** — `PostgresSaver` gives the graph a memory: every `invoke()`/`stream()`
call is checkpointed to Postgres under a `thread_id`, so a conversation could in principle
be resumed later. The connection is opened with `autocommit=True` because
`checkpointer.setup()` runs a `CREATE INDEX CONCURRENTLY` migration, and Postgres refuses
to run that particular statement inside a transaction block.

**CLI entry point** (`if __name__ == "__main__":`) — prompts for a travel request in the
terminal, invokes the compiled `app`, prints every message in the final state.

---

## 7. `frontend.py` — the web UI

Imports the exact same compiled `app` from `planner.py` — it does **not** duplicate any
agent logic, it's purely a UI layer on top.

Key design choice: `plan_trip()` is a **Gradio generator function** that calls
`travel_app.stream(..., stream_mode="values")` instead of `.invoke()`. `stream_mode="values"`
yields the *full* state after each agent finishes, and because the function `yield`s
multiple times, Gradio updates the visible tabs progressively — Flights fills in, then
Hotels, then Weather, then Itinerary — rather than the UI freezing until the whole
pipeline completes.

Other features:
- Structured trip form (trip length / budget / travel style / traveler count) gets folded
  into a single enriched string, since the backend only accepts one `user_query` string.
- A status badge (Ready / Planning / Ready / Error) shows pipeline progress.
- Once the itinerary is ready, both a `.txt` and a `.pdf` version are generated
  (`reportlab`) and offered as downloads.

---

## 8. Configuration (`.env`)

| Variable | Used for |
|---|---|
| `GROQ_API_KEY` | The LLM (`llama-3.3-70b-versatile` via Groq) |
| `DATABASE_URL` | Postgres connection string for the LangGraph checkpointer |
| `TAVILY_API_KEY` | Hotel/web search MCP server |
| `AVIATION_STACK_API_KEY` | Aviationstack MCP server (flight/airport/airline data) |
| `OPENWEATHER_API_KEY` | Weather MCP server |

---

## 9. Running it

```bash
# CLI version — single prompt in the terminal
python Src/planner.py

# Web UI version
python frontend.py
```

Both share the same compiled graph, agents, and Postgres-backed memory — only the input/
output layer differs.

---

## 10. Mental model summary

- **LangGraph** = the flowchart/orchestrator (which agent runs after which, and what state
  they share).
- **MCP** = the plumbing that lets an agent say "call this tool on that server" without
  caring whether the server is a local subprocess or a remote HTTP service.
- **Agents** (`flight_agent`, `hotel_agent`, etc.) = plain functions that mix MCP tool
  calls with LLM reasoning.
- **`mcp_client.py`** = the adapter layer that hides MCP's raw JSON-RPC/content-block
  format behind clean Python functions the agents can call like any other function.
- **`frontend.py`** = presentation only; all the actual intelligence lives in
  `planner.py` + `mcp_client.py`.
