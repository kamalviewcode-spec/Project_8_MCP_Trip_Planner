
# LangGraph Multi-Agent Travel Booking System with Long-Term Memory

import os
import sys
from typing import TypedDict, Annotated
import operator
import asyncio
import psycopg

# mcp_client.py lives at the project root, one level above this file (Src/),
# so it needs to be added to sys.path before it can be imported below.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)

# from tools.tavily_tool import tavily_search

#from mcp_client import tavily_mcp_search

from mcp_client import (
    tavily_mcp_search,
    get_airports,
    get_airlines,
    aviation_mcp_call,extract_destination,forecast_mcp_search,weather_mcp_search,
    get_llm,
)


#from tools.flight_tool import search_flights


from dotenv import load_dotenv
#load_dotenv()
load_dotenv(override=True)
DATABASE_URL = os.getenv("DATABASE_URL")

# LLM — provider is chosen via the LLM_PROVIDER env var (see mcp_client.get_llm),
# so the same code path works for Groq, Claude, Gemini, OpenAI, or DeepSeek.
llm = get_llm()

# State: the shared dict that flows through every node in the graph. Each
# agent below reads from it and returns a partial update that LangGraph merges
# back in. `messages` uses operator.add as its reducer, so new messages get
# appended to the list instead of overwriting it; every other field is a
# plain "last write wins" plain value.
class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    flight_results: str
    hotel_results: str
    itinerary: str
    llm_calls: int
    weather_results: str

# Flight Agent
# def flight_agent(state: TravelState):
#     query = state["user_query"]
#     flight_data = search_flights(query)
#     return {
#         "flight_results": flight_data,
#         "messages": [
#             AIMessage(content=f"Flight results fetched")
#         ],
#         "llm_calls": state.get("llm_calls", 0) + 1
#     }


# Flight Tool Router Prompt
FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Generate:

1. Likely departure airport
2. Likely arrival airport
3. Airlines serving this route
4. Typical flight duration
5. Estimated airfare range
6. Peak season pricing warning
7. Booking advice

Return concise travel guidance.
"""



# Flight Agent
# Doesn't call a real flight-search API. Instead it hands the raw MCP data
# (all airports/airlines) to the LLM and asks it to reason about a plausible
# route, price range, etc. This keeps the demo working without needing a
# live flight-pricing API/key.
def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")

    query = state["user_query"]

    try:
        # asyncio.run() opens a fresh event loop for each call, which is
        # necessary because this function itself is a plain synchronous
        # function (LangGraph nodes here are sync), but the MCP client is async.
        airports = asyncio.run(
            aviation_mcp_call(
                "list_airports"
            )
        )

        airlines = asyncio.run(
            aviation_mcp_call(
                "list_airlines"
            )
        )

        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            airport_data=str(airports)[:3000],
            airline_data=str(airlines)[:3000]
        )

        response = llm.invoke([
            SystemMessage(
                content="You are an expert travel flight planner."
            ),
            HumanMessage(content=prompt)
        ])

        flight_data = response.content

    except Exception as e:

        flight_data = f"Flight information unavailable: {str(e)}"

    return {
        "flight_results": flight_data,
        "messages": [
            AIMessage(
                content="Flight recommendations generated"
            )
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }




# Hotel Agent
# Important: search on the extracted DESTINATION only, not the raw user_query.
# The raw query often mentions the origin too (e.g. "from Des Moines to Bali"),
# and searching on the whole string was previously pulling up origin-city hotels.
def hotel_agent(state: TravelState):
    destination = extract_destination(state["user_query"])
    query = f"Best hotels in {destination}"
    #hotel_results = tavily_search(query)

    hotel_results = asyncio.run(
        tavily_mcp_search(query)
    )

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(content="Hotel information fetched")
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }







# Weather Agent
# Same destination-extraction approach as hotel_agent, for the same reason:
# the destination city, not the origin, is what we need weather/forecast for.
def weather_agent(state: TravelState):

    city = extract_destination(state["user_query"])

    weather_data = asyncio.run(
        weather_mcp_search(city)
    )

    forecast_data = asyncio.run(
        forecast_mcp_search(city)
    )

    return {
        "weather_results": f"""
        Current Weather:
        {weather_data}

        Forecast:
        {forecast_data}
        """,
        "messages": [
            AIMessage(
                content="Weather information fetched"
            )
        ]
    }





# Itinerary Agent
def itinerary_agent(state: TravelState):

    prompt = f"""
    Create a travel itinerary.
    User Query:
    {state['user_query']}

    Flight Results:
    {state['flight_results']}

    Hotel Results:
    {state['hotel_results']}

    Weather Information:
    {state['weather_results']}
    """

    response = llm.invoke([
        SystemMessage(
            content="You are an expert travel planner"
        ),
        HumanMessage(content=prompt)
    ])

    return {
        "itinerary": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }







# Build the graph: a straight-line pipeline (no branching/looping) where each
# agent's output becomes part of the state the next agent reads from.
# flight -> hotel -> weather -> itinerary, then END.
graph = StateGraph(TravelState)

graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("itinerary_agent", itinerary_agent)


graph.add_edge(START, "flight_agent")
graph.add_edge("flight_agent", "hotel_agent")
graph.add_edge("hotel_agent", "weather_agent")
graph.add_edge("weather_agent", "itinerary_agent")
graph.add_edge("itinerary_agent", END)


# Persistent connection so both CLI and Streamlit can share the compiled app
# autocommit=True is required here: PostgresSaver.setup() runs a
# `CREATE INDEX CONCURRENTLY` migration, and Postgres refuses to run that
# statement inside a transaction block.
_conn = psycopg.connect(DATABASE_URL, autocommit=True)
checkpointer = PostgresSaver(_conn)
# Creates the checkpoint tables/indexes in Postgres if they don't exist yet.
# Safe to call every time the module is imported (no-ops once already set up).
checkpointer.setup()

# Compiling with a checkpointer means every invoke()/stream() call is saved
# to Postgres under its thread_id (see config below), enabling conversation
# memory/resumability across runs.
app = graph.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    # config = {
    #     "configurable": {
    #         "thread_id": "user_aarohi"
    #     }
    # }

    # A random thread_id means every CLI run starts a brand-new conversation
    # in the checkpointer rather than resuming a previous one. Use a fixed
    # thread_id instead if you want a run to continue an earlier session's state.
    import uuid
    config = {
        "configurable": {
            "thread_id": str(uuid.uuid4())
        }
    }


    user_input = input("Enter travel request: ")

    result = app.invoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "flight_results": "",
            "hotel_results": "",
            "itinerary": "",
            "llm_calls": 0
        },
        config=config
    )

    print("\nFINAL RESPONSE:\n")

    for msg in result["messages"]:
        print(msg.content)