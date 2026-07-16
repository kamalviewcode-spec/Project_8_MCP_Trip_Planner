# pip install mcp

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Aviation Server")

AIRPORTS = [
    {"code": "JFK", "name": "John F. Kennedy International Airport", "city": "New York", "country": "USA"},
    {"code": "LAX", "name": "Los Angeles International Airport", "city": "Los Angeles", "country": "USA"},
    {"code": "LHR", "name": "London Heathrow Airport", "city": "London", "country": "UK"},
    {"code": "CDG", "name": "Charles de Gaulle Airport", "city": "Paris", "country": "France"},
    {"code": "DXB", "name": "Dubai International Airport", "city": "Dubai", "country": "UAE"},
    {"code": "SIN", "name": "Singapore Changi Airport", "city": "Singapore", "country": "Singapore"},
    {"code": "HND", "name": "Tokyo Haneda Airport", "city": "Tokyo", "country": "Japan"},
    {"code": "DEL", "name": "Indira Gandhi International Airport", "city": "New Delhi", "country": "India"},
    {"code": "BOM", "name": "Chhatrapati Shivaji Maharaj International Airport", "city": "Mumbai", "country": "India"},
    {"code": "SYD", "name": "Sydney Kingsford Smith Airport", "city": "Sydney", "country": "Australia"},
]

AIRLINES = [
    {"code": "AA", "name": "American Airlines", "country": "USA"},
    {"code": "DL", "name": "Delta Air Lines", "country": "USA"},
    {"code": "BA", "name": "British Airways", "country": "UK"},
    {"code": "AF", "name": "Air France", "country": "France"},
    {"code": "EK", "name": "Emirates", "country": "UAE"},
    {"code": "SQ", "name": "Singapore Airlines", "country": "Singapore"},
    {"code": "NH", "name": "All Nippon Airways", "country": "Japan"},
    {"code": "AI", "name": "Air India", "country": "India"},
    {"code": "QF", "name": "Qantas", "country": "Australia"},
]


@mcp.tool()
def list_airports():
    return AIRPORTS


@mcp.tool()
def list_airlines():
    return AIRLINES


if __name__ == "__main__":
    mcp.run()
