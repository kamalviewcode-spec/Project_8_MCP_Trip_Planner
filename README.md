# AI Trip Planner (Multi-Agent Travel Booking System with MCP)

A multi-agent travel-planning application built with **LangGraph**, the **Model Context
Protocol (MCP)**, and support for **5 interchangeable LLM providers** (Groq, Anthropic
Claude, Google Gemini, OpenAI, and DeepSeek). Given a single free-text travel request, it
produces flight guidance, hotel recommendations, a weather forecast, and a complete
day-by-day itinerary — downloadable as both `.txt` and `.pdf`.

---

## What it does

You type something like:

> "A 5-day trip to Bali for a couple in December"

...and the system runs it through a 4-stage agent pipeline:

1. **Flight Agent** — reasons about likely departure/arrival airports, airlines serving
   the route, typical flight duration, and an estimated fare range, using live
   airport/airline reference data.
2. **Hotel Agent** — searches the web for the best hotels at the destination.
3. **Weather Agent** — fetches current conditions and a short-term forecast for the
   destination.
4. **Itinerary Agent** — combines everything the previous 3 agents found into a coherent,
   day-by-day travel itinerary.

Available through both a terminal CLI and a Gradio web UI, with the final itinerary
downloadable as text or PDF.

---

## Architecture at a glance

```
User query
   │
   ▼
┌─────────────┐    ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐
│ Flight Agent│───▶│ Hotel Agent │───▶│ Weather Agent│───▶│ Itinerary Agent  │
└─────────────┘    └─────────────┘    └──────────────┘    └──────────────────┘
       │                  │                   │                    │
       ▼                  ▼                   ▼                    ▼
 AviationStack MCP    Tavily MCP       OpenWeather MCP        LLM (chosen
   (stdio server)   (hosted server,   (stdio server, local)   provider) only
                      HTTP transport)
```

Each agent is a plain Python function orchestrated by **LangGraph** as nodes in a
`StateGraph`. Live data (airports, airlines, hotel search, weather) is fetched through
**MCP servers** rather than hardcoded API-specific code inside each agent. State is
checkpointed to **PostgreSQL** on every run via LangGraph's `PostgresSaver`.

See [`Guide.md`](Guide.md) for a full technical walkthrough of the codebase, file by file.

---

## Key features

- **Multi-agent pipeline** — 4 specialized agents chained together by LangGraph.
- **MCP-powered data access** — 3 separate MCP servers (weather, aviation, web search),
  over both `stdio` (local subprocess) and `streamable_http` (remote) transports.
- **5 swappable LLM providers** — Groq, Claude, Gemini, OpenAI, or DeepSeek, selected at
  launch time via an `LLM_PROVIDER` environment variable — no code changes required.
- **Persistent memory** — every conversation is checkpointed to Postgres by `thread_id`.
- **Two front ends** — a terminal CLI (`Src/planner.py`) and a Gradio web app
  (`frontend.py`), both sharing the exact same compiled graph.
- **Progressive UI updates** — the web UI streams results tab-by-tab as each agent
  finishes.
- **Downloadable itinerary** — auto-generated `.txt` and `.pdf` versions of the final plan.

---

## Project structure

```
Project_8_MCP_Trip_Planner/
├── Src/
│   └── planner.py                 # LangGraph pipeline, agents, CLI entry point
├── mcp_client.py                  # Bridges agents to the 3 MCP servers + LLM provider selection
├── frontend.py                    # Gradio web UI
├── custom_mcp_weather_server.py   # Local MCP server: current weather + forecast (OpenWeather)
├── aviationstack-mcp/             # Cloned third-party MCP server: airports/airlines/flights
├── pyproject.toml                 # Python dependencies
├── .env                           # API keys & DB connection string (not committed)
└── Guide.md                       # Deep technical walkthrough of the codebase
```

---

## Technology stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph (`StateGraph`) |
| Data access protocol | MCP (Model Context Protocol) |
| LLM providers | Groq (Llama), Anthropic Claude, Google Gemini, OpenAI, DeepSeek |
| Web search | Tavily (hosted MCP server) |
| Weather data | OpenWeatherMap (custom local MCP server) |
| Flight/airport data | AviationStack (cloned MCP server) |
| Conversation memory | PostgreSQL + `langgraph-checkpoint-postgres` |
| Web UI | Gradio |
| PDF export | ReportLab |
| Env/config | python-dotenv |

---

## Setup & running

### 1. Prerequisites
- Python 3.12+
- A running PostgreSQL instance
- API keys for your chosen LLM provider(s), plus Tavily, AviationStack, and OpenWeatherMap

### 2. Install dependencies
```bash
python -m venv venv
source venv/Scripts/activate   # or venv\Scripts\activate.bat on Windows cmd
pip install -e .
```

### 3. Configure `.env`
```
DATABASE_URL=postgresql://postgres:<password>@localhost:5432/trip_planner
GROQ_API_KEY=...
TAVILY_API_KEY=...
AVIATION_STACK_API_KEY=...
OPENWEATHER_API_KEY=...

# Optional, only needed for the other LLM providers:
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
```

### 3.5. Clone the AviationStack MCP server

This project depends on a separate open-source MCP server for flight/airport/airline
data. It's not tracked inside this repo — clone it into the project root and set up its
own virtual environment:

```bash
git clone https://github.com/pradumnasaraf/aviationstack-mcp.git
cd aviationstack-mcp
python -m venv .venv
.venv\Scripts\activate.bat   # or source .venv/Scripts/activate
pip install -e .
cd ..
```

`mcp_client.py` expects it at `<project root>/aviationstack-mcp/.venv/Scripts/python.exe`.

### 4. Run it

CLI:
```bash
python Src/planner.py
```

Web UI:
```bash
python frontend.py
```

Choosing a different LLM provider (any run):
```bash
set LLM_PROVIDER=claude   # or gemini / openai / deepseek / groq
python frontend.py
```

---

## Further reading

- [`Guide.md`](Guide.md) — deep technical walkthrough of the codebase, file by file.
