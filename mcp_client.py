import os
import sys
import json
import asyncio

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

#load_dotenv()
load_dotenv(override=True)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
AVIATION_STACK_API_KEY = os.getenv("AVIATION_STACK_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# Absolute path to this project's root, so server paths below work no matter
# which directory this script is imported/run from.
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# MultiServerMCPClient manages connections to 3 separate MCP servers.
# Two transports are used:
#  - "streamable_http": talks to a remote MCP server over HTTP (Tavily's hosted server).
#  - "stdio": launches a local MCP server as a subprocess and talks to it over
#    its stdin/stdout (aviationstack + weather, both run in-process on this machine).
client = MultiServerMCPClient(
    {
        "tavily": {
            "transport": "streamable_http",
            "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={TAVILY_API_KEY}"
        },

        "aviationstack": {
            "transport": "stdio",
            # Uses the aviationstack-mcp project's OWN virtualenv, since it's a
            # separate cloned repo with its own dependencies, not this project's venv.
            "command": os.path.join(ROOT_DIR, "aviationstack-mcp", ".venv", "Scripts", "python.exe"),
            "args": [
                "-m",
                "aviationstack_mcp"
            ],
            "env": {
                "AVIATION_STACK_API_KEY": AVIATION_STACK_API_KEY
            }
        },

        "weather": {
            "transport": "stdio",
            # Reuses this project's own venv (sys.executable) since
            # custom_mcp_weather_server.py lives in this same project.
            "command": sys.executable,
            "args": [
                os.path.join(ROOT_DIR, "custom_mcp_weather_server.py")
            ],
            "env": {
                "OPENWEATHER_API_KEY": OPENWEATHER_API_KEY
            }
        }



    }

)


# tools discovery
# async def main():

#     tools = await client.get_tools()

#     print("\nAvailable MCP Tools:\n")

#     for tool in tools:
#         print(tool.name)


# async def main():
#     tools = await client.get_tools()

#     search_tool = next(
#         tool
#         for tool in tools
#         if tool.name == "tavily_search"
#     )

#     result = await search_tool.ainvoke(
#         {
#             "query": "Best hotels in Delhi"
#         }
#     )

#     print(result)

# asyncio.run(main()) 


# search_tool = None

# async def initialize_mcp():
#     global search_tool
#     if search_tool is not None:
#         return

#     tools = await client.get_tools()
#     print("\nAvailable MCP Tools:")

#     for tool in tools:
#         print(tool.name)

#     search_tool = next(
#         tool
#         for tool in tools
#         if tool.name == "tavily_search"
#     )



search_tool = None
aviation_tools = {}

# Lazily fetches and caches tool handles from all connected MCP servers.
# client.get_tools() spawns/queries every configured server, so we only want
# to do this once per process rather than on every search call.
async def initialize_mcp():

    global search_tool
    global aviation_tools

    if search_tool is not None and aviation_tools:
        return

    tools = await client.get_tools()

    print("\nAvailable MCP Tools:\n")

    for tool in tools:
        print(tool.name)

    search_tool = next(
        tool
        for tool in tools
        if tool.name == "tavily_search"
    )

    aviation_tools = {
        tool.name: tool
        for tool in tools
        if tool.name != "tavily_search"
    }





# MCP tool results come back as a list of "content blocks", e.g.
# [{"type": "text", "text": "...json or plain string..."}], not a plain string.
# This unwraps that envelope so callers just get the underlying text.
def _mcp_result_to_raw_text(result):
    if isinstance(result, list):
        texts = [
            item.get("text", "")
            for item in result
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(texts) if texts else str(result)

    return result if isinstance(result, str) else str(result)


# Tavily's search tool returns a JSON string with a "results" array
# (title/url/content per hit). This turns that into plain readable text
# instead of a raw JSON blob, both for display and for feeding into the LLM.
def _format_tavily_result(result):
    raw = _mcp_result_to_raw_text(result)

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

    if isinstance(parsed, dict) and "results" in parsed:
        entries = parsed.get("results") or []
        if not entries:
            return parsed.get("answer") or "No hotel results found."

        lines = []
        for entry in entries:
            title = entry.get("title", "Untitled")
            url = entry.get("url", "")
            content = entry.get("content", "")
            lines.append(f"{title}\n{content}\n{url}")

        return "\n\n".join(lines)

    return raw


async def tavily_mcp_search(query: str):
    await initialize_mcp()
    result = await search_tool.ainvoke(
        {
            "query": query
        }
    )
    return _format_tavily_result(result)




# Generic entry point for calling any tool on the aviationstack MCP server
# by name (e.g. "list_airports", "list_airlines") without needing a dedicated
# wrapper function per tool.
async def aviation_mcp_call(
    tool_name: str,
    tool_args: dict = None
):

    tools = await client.get_tools()

    tool = next(
        t for t in tools
        if t.name == tool_name
    )

    result = await tool.ainvoke(
        tool_args or {}
    )

    return result



async def get_airports():

    await initialize_mcp()

    tool = aviation_tools.get("list_airports")

    if not tool:
        return "Airport tool unavailable"

    result = await tool.ainvoke({})

    return result


async def get_airlines():

    await initialize_mcp()

    tool = aviation_tools.get("list_airlines")

    if not tool:
        return "Airline tool unavailable"

    result = await tool.ainvoke({})

    return result





weather_tool = None
forecast_tool = None


async def initialize_weather_tools():

    global weather_tool, forecast_tool

    if weather_tool is not None:
        return

    tools = await client.get_tools()

    weather_tool = next(
        t for t in tools
        if t.name == "get_current_weather"
    )

    forecast_tool = next(
        t for t in tools
        if t.name == "get_forecast"
    )


# custom_mcp_weather_server.py's get_current_weather tool returns a JSON dict
# (city/temperature_c/condition/etc). Reformat into one readable summary line.
def _format_current_weather(result):
    raw = _mcp_result_to_raw_text(result)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

    if not isinstance(data, dict) or "temperature_c" not in data:
        return raw

    return (
        f"{data.get('city', 'Unknown')}: {data.get('condition', 'N/A')}, "
        f"{data.get('temperature_c')}°C (feels like {data.get('feels_like_c')}°C), "
        f"humidity {data.get('humidity')}%, wind {data.get('wind_speed')} m/s"
    )


# Same idea for get_forecast's JSON payload (a list of {datetime, temperature, weather}).
def _format_forecast(result):
    raw = _mcp_result_to_raw_text(result)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

    if not isinstance(data, dict) or "forecast" not in data:
        return raw

    lines = [
        f"{entry.get('datetime')}: {entry.get('temperature')}°C, {entry.get('weather')}"
        for entry in data.get("forecast") or []
    ]
    return "\n".join(lines) if lines else "No forecast data available."


async def weather_mcp_search(city: str):

    await initialize_weather_tools()

    result = await weather_tool.ainvoke(
        {
            "city": city
        }
    )
    return _format_current_weather(result)


async def forecast_mcp_search(city: str):

    await initialize_weather_tools()

    result = await forecast_tool.ainvoke(
        {
            "city": city
        }
    )
    return _format_forecast(result)




# Picks which LLM provider/model to use based on the LLM_PROVIDER env var
# (set per-run, e.g. by the .bat launcher scripts), defaulting to Groq if unset.
# Each provider's API key is read automatically from the env by its own
# LangChain integration (GROQ_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY,
# OPENAI_API_KEY, DEEPSEEK_API_KEY) — no extra wiring needed here.
def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()

    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))

    if provider in ("claude", "anthropic"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))

    if provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=os.getenv("GOOGLE_MODEL", "gemini-1.5-flash"))

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))

    if provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek
        return ChatDeepSeek(model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. Expected one of: "
        "groq, claude, gemini, openai, deepseek."
    )


# LLM
llm = get_llm()

###################################
# Destination Extractor
###################################

# Uses the LLM (instead of regex) to pull just the destination city/country
# out of a free-form query like "Travelling from Des Moines to Bali for 7 days".
# This matters because the raw query often mentions the ORIGIN too, and a naive
# string search would grab the wrong city for weather/hotel lookups.
def extract_destination(query: str):

    prompt = f"""
    Extract only the destination city or country.

    Query:
    {query}

    Return only destination name.
    """

    response = llm.invoke(prompt)

    return response.content.strip()


if __name__ == "__main__":
    asyncio.run(main())