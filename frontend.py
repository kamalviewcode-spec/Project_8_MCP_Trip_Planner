import os
import sys
import uuid
import tempfile
import traceback

import gradio as gr
from langchain_core.messages import HumanMessage
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "Src"))

# Reuses the exact same compiled LangGraph app (and its Postgres checkpointer)
# that Src/planner.py's CLI entry point uses — this file only adds a UI on top,
# it doesn't duplicate any of the agent logic.
from planner import app as travel_app

CUSTOM_CSS = """
.gradio-container {
    max-width: 1180px !important;
    margin: auto !important;
}

#hero {
    text-align: center;
    padding: 28px 24px 24px 24px;
    border-radius: 18px;
    margin-bottom: 18px;
    background: linear-gradient(135deg, #4f46e5 0%, #0ea5e9 100%);
}
#hero h1 {
    color: #ffffff !important;
    font-size: 2.4rem;
    font-weight: 800;
    margin-bottom: 6px;
    letter-spacing: -0.02em;
}
#hero p {
    color: rgba(255,255,255,0.92) !important;
    font-size: 1.05rem;
    max-width: 640px;
    margin: 0 auto;
}

.trip-form {
    padding: 18px;
    border-radius: 14px;
    border: 1px solid var(--border-color-primary);
    background: var(--background-fill-secondary);
}

#status-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 0.85rem;
    font-weight: 600;
    background: var(--background-fill-primary);
    border: 1px solid var(--border-color-primary);
}

.result-box {
    font-size: 0.95rem;
    line-height: 1.6;
}

.error-card {
    border-radius: 12px;
    border: 1px solid color-mix(in srgb, #dc2626 40%, var(--border-color-primary));
    background: color-mix(in srgb, #dc2626 12%, var(--background-fill-primary));
    padding: 12px 16px;
    margin-bottom: 12px;
}

@keyframes pulse { 50% { opacity: .35; } }
#status-badge .dot-running {
    animation: pulse 1.2s ease-in-out infinite;
}
"""

THEME = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="sky",
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "sans-serif"],
)

TRIP_LENGTHS = ["Weekend (2-3 days)", "Short trip (4-6 days)", "Extended (7-14 days)", "Long trip (15+ days)"]
BUDGET_LEVELS = ["Budget-friendly", "Mid-range", "Luxury"]
TRAVEL_STYLES = ["Leisure & relaxation", "Adventure & outdoors", "Culture & sightseeing", "Family-friendly", "Romantic getaway"]

EXAMPLES = [
    ["A romantic trip to Paris in the spring", "Short trip (4-6 days)", "Mid-range", "Romantic getaway", 2],
    ["Weekend getaway to Goa with friends", "Weekend (2-3 days)", "Budget-friendly", "Adventure & outdoors", 4],
    ["Family trip to Japan during cherry blossom season", "Extended (7-14 days)", "Luxury", "Family-friendly", 4],
]

STATUS_IDLE = "Ready"
STATUS_RUNNING = "Planning your trip..."
STATUS_DONE = "Itinerary ready"
STATUS_ERROR = "Something went wrong"

# Stage-specific placeholder shown in each panel until its agent finishes.
WAITING_FLIGHTS = "Searching flights..."
WAITING_HOTELS = "Finding hotels..."
WAITING_WEATHER = "Checking the weather..."
WAITING_ITINERARY = "Waiting for all agents to finish before writing your itinerary..."


# Renders the small colored-dot status pill shown above the results tabs.
# Returned as raw HTML since Gradio's gr.HTML component just injects it as-is.
def _badge(text: str, kind: str = "idle") -> str:
    colors = {
        "idle": "#64748b",
        "running": "#0ea5e9",
        "done": "#16a34a",
        "error": "#dc2626",
    }
    color = colors.get(kind, "#64748b")
    dot_class = "dot dot-running" if kind == "running" else "dot"
    return (
        f'<div id="status-badge" role="status" aria-live="polite">'
        f'<span class="{dot_class}" style="width:8px;height:8px;border-radius:50%;background:{color};display:inline-block;"></span>'
        f'<span>{text}</span>'
        f'</div>'
    )


# Converts the itinerary text into a downloadable PDF using reportlab's
# "flowable" model: build a list of Paragraph/Spacer objects (the "story"),
# then let SimpleDocTemplate handle page breaks/layout automatically.
def _write_itinerary_pdf(itinerary_text: str, path: str):
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()

    story = [Paragraph("Trip Itinerary", styles["Title"]), Spacer(1, 0.25 * inch)]

    for paragraph in itinerary_text.split("\n"):
        text = paragraph.strip()
        if text:
            # reportlab's Paragraph interprets its input as mini-HTML, so any
            # literal &, <, > in the itinerary text must be escaped first or
            # it'll be misread as markup and break rendering.
            escaped = (
                text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            story.append(Paragraph(escaped, styles["BodyText"]))
        else:
            story.append(Spacer(1, 0.15 * inch))

    doc.build(story)


# Folds the structured form fields (trip length/budget/style/travelers) into
# the free-text query, since the underlying LangGraph agents only accept a
# single `user_query` string, not separate structured fields.
def _build_query(query, trip_length, budget, style, travelers):
    parts = [query.strip()]
    parts.append(f"Trip length preference: {trip_length}.")
    parts.append(f"Budget level: {budget}.")
    parts.append(f"Travel style: {style}.")
    parts.append(f"Number of travelers: {int(travelers)}.")
    return " ".join(p for p in parts if p)


# This is a Gradio generator function: instead of returning once, it `yield`s
# a tuple of output values multiple times, and Gradio updates the UI after
# each yield. Combined with travel_app.stream() (rather than .invoke()), this
# is what makes each results tab fill in progressively as its agent finishes,
# instead of the whole UI freezing until the entire pipeline is done.
def plan_trip(query, trip_length, budget, style, travelers):
    query = (query or "").strip()

    if not query:
        gr.Warning("Please describe your trip first.")
        yield (
            "", "", "", "",
            gr.update(visible=False),
            gr.update(visible=False),
            _badge(STATUS_IDLE, "idle"),
            gr.update(visible=False),
        )
        return

    enriched_query = _build_query(query, trip_length, budget, style, travelers)

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    # Initialized before the try block so the except path can still yield the
    # current panel values even if stream() raises before its first iteration.
    flight = WAITING_FLIGHTS
    hotel = WAITING_HOTELS
    weather = WAITING_WEATHER
    itinerary = WAITING_ITINERARY

    yield (
        flight, hotel, weather, itinerary,
        gr.update(visible=False),
        gr.update(visible=True),
        _badge(STATUS_RUNNING, "running"),
        gr.update(visible=False),
    )

    try:
        # stream_mode="values" yields the FULL state dict after each node
        # finishes (not just what changed), so each iteration below just
        # re-reads whichever fields have been filled in so far.
        for state in travel_app.stream(
            {
                "messages": [HumanMessage(content=enriched_query)],
                "user_query": enriched_query,
                "flight_results": "",
                "hotel_results": "",
                "itinerary": "",
                "llm_calls": 0,
                "weather_results": "",
            },
            config=config,
            stream_mode="values",
        ):
            flight = state.get("flight_results") or WAITING_FLIGHTS
            hotel = state.get("hotel_results") or WAITING_HOTELS
            weather = state.get("weather_results") or WAITING_WEATHER
            itinerary = state.get("itinerary") or WAITING_ITINERARY

            done = sum(bool(state.get(k)) for k in ("flight_results", "hotel_results", "weather_results", "itinerary"))

            yield (
                flight, hotel, weather, itinerary,
                gr.update(visible=False),
                gr.update(visible=True),
                _badge(f"Planning your trip... ({done}/4 agents done)", "running"),
                gr.update(visible=False),
            )

        download_update = gr.update(visible=False)
        if itinerary and itinerary != WAITING_ITINERARY:
            base = os.path.join(tempfile.gettempdir(), f"itinerary_{uuid.uuid4().hex}")
            txt_path = f"{base}.txt"
            pdf_path = f"{base}.pdf"

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(itinerary)
            _write_itinerary_pdf(itinerary, pdf_path)

            download_update = gr.update(value=[txt_path, pdf_path], visible=True)

        yield (
            flight, hotel, weather, itinerary,
            gr.update(visible=False),
            gr.update(visible=True),
            _badge(STATUS_DONE, "done"),
            download_update,
        )

    except Exception as exc:
        # Full traceback goes to the terminal; the UI banner only gets the
        # short exception message.
        traceback.print_exc()

        # Keep whatever partial results already streamed in; panels whose
        # agent never ran drop their "Searching..." placeholder so they don't
        # look stuck alongside the error banner.
        unavailable = "*Not available — planning stopped early.*"
        flight = unavailable if flight == WAITING_FLIGHTS else flight
        hotel = unavailable if hotel == WAITING_HOTELS else hotel
        weather = unavailable if weather == WAITING_WEATHER else weather
        itinerary = unavailable if itinerary == WAITING_ITINERARY else itinerary

        yield (
            flight, hotel, weather, itinerary,
            gr.update(value=f"**Trip planning failed:** {exc}", visible=True),
            gr.update(visible=True),
            _badge(STATUS_ERROR, "error"),
            gr.update(visible=False),
        )


def clear_all():
    return (
        "", TRIP_LENGTHS[1], BUDGET_LEVELS[1], TRAVEL_STYLES[0], 2,
        "", "", "", "",
        gr.update(visible=False),
        gr.update(visible=False),
        _badge(STATUS_IDLE, "idle"),
        gr.update(visible=False),
    )


with gr.Blocks(title="AI Trip Planner") as demo:
    with gr.Column(elem_id="hero"):
        gr.Markdown("# AI Trip Planner")
        gr.Markdown("Tell us about your dream trip — get flights, hotels, weather, and a day-by-day itinerary powered by a multi-agent AI pipeline.")

    with gr.Column(elem_classes="trip-form"):
        query_box = gr.Textbox(
            label="Where do you want to go?",
            placeholder="e.g. A 5-day trip to Bali for a couple in December",
            lines=2,
        )

        with gr.Row():
            trip_length = gr.Dropdown(TRIP_LENGTHS, value=TRIP_LENGTHS[1], label="Trip length")
            budget = gr.Dropdown(BUDGET_LEVELS, value=BUDGET_LEVELS[1], label="Budget")
            style = gr.Dropdown(TRAVEL_STYLES, value=TRAVEL_STYLES[0], label="Travel style")
            travelers = gr.Slider(1, 10, value=2, step=1, label="Travelers")

        with gr.Row():
            clear_btn = gr.Button("Clear", variant="secondary", scale=1)
            submit_btn = gr.Button("Plan my trip", variant="primary", scale=3)

    gr.Examples(
        examples=EXAMPLES,
        inputs=[query_box, trip_length, budget, style, travelers],
        label="Try an example",
    )

    status_html = gr.HTML(_badge(STATUS_IDLE, "idle"))

    with gr.Column(visible=False) as results_col:
        error_md = gr.Markdown(visible=False, elem_classes="error-card")

        # Tabs follow pipeline order so the default-open tab fills in first.
        with gr.Tabs():
            with gr.Tab("Flights"):
                flight_out = gr.Markdown(
                    show_label=False, container=True, max_height=480,
                    buttons=["copy"], line_breaks=True, elem_classes="result-box",
                )
            with gr.Tab("Hotels"):
                hotel_out = gr.Markdown(
                    show_label=False, container=True, max_height=480,
                    buttons=["copy"], line_breaks=True, elem_classes="result-box",
                )
            with gr.Tab("Weather"):
                weather_out = gr.Markdown(
                    show_label=False, container=True, max_height=480,
                    buttons=["copy"], line_breaks=True, elem_classes="result-box",
                )
            with gr.Tab("Itinerary"):
                itinerary_out = gr.Markdown(
                    show_label=False, container=True, max_height=480,
                    buttons=["copy"], line_breaks=True, elem_classes="result-box",
                )

        download_file = gr.File(label="Download itinerary (TXT & PDF)", file_count="multiple", visible=False)

    outputs = [flight_out, hotel_out, weather_out, itinerary_out, error_md, results_col, status_html, download_file]
    inputs = [query_box, trip_length, budget, style, travelers]

    submit_btn.click(fn=plan_trip, inputs=inputs, outputs=outputs, api_name="plan_trip")
    query_box.submit(fn=plan_trip, inputs=inputs, outputs=outputs)

    clear_btn.click(
        fn=clear_all,
        inputs=None,
        outputs=[query_box, trip_length, budget, style, travelers,
                 flight_out, hotel_out, weather_out, itinerary_out,
                 error_md, results_col, status_html, download_file],
    )


if __name__ == "__main__":
    demo.launch(theme=THEME, css=CUSTOM_CSS, footer_links=[])
