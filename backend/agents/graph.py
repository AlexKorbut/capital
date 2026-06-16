"""The three LangGraph graphs (plan §2).

Builders return *uncompiled* `StateGraph`s; `compile_all(checkpointer)` compiles
them against a durable checkpointer so the human-review interrupt persists across
restarts. Node bodies live in `agents/nodes/` (thin wrappers over `services/`).
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents import nodes
from agents.state import AdvisorState, InputState, ScenarioState

# --- Input graph ---------------------------------------------------------------


def build_input_graph() -> StateGraph:
    g = StateGraph(InputState)

    g.add_node("input_router", nodes.input_router)
    g.add_node("transcribe", nodes.transcribe)
    g.add_node("process_file", nodes.process_file)
    g.add_node("parse", nodes.parse)
    g.add_node("validate", nodes.validate)
    g.add_node("human_review", nodes.human_review)
    g.add_node("enrich", nodes.enrich)
    g.add_node("save_to_db", nodes.save_to_db)

    g.add_edge(START, "input_router")
    g.add_conditional_edges(
        "input_router",
        nodes.route_input,
        {"transcribe": "transcribe", "process_file": "process_file", "parse": "parse"},
    )
    g.add_edge("transcribe", "parse")
    g.add_edge("process_file", "parse")
    g.add_edge("parse", "validate")
    # Always-preview flow: enrich first (so the preview shows usd_value), then
    # pause before human_review for the user to confirm/edit, then persist.
    g.add_edge("validate", "enrich")
    g.add_edge("enrich", "human_review")
    g.add_edge("human_review", "save_to_db")
    g.add_edge("save_to_db", END)
    return g


# --- Advisor graph (geo || news fan-out/fan-in) -------------------------------


def build_advisor_graph() -> StateGraph:
    g = StateGraph(AdvisorState)

    # Node ids must not collide with state keys (`news`, `geo_analysis`, ...),
    # so the parallel agents get '_agent'-suffixed ids.
    g.add_node("portfolio_loader", nodes.portfolio_loader)
    g.add_node("geo_agent", nodes.geo)
    g.add_node("news_agent", nodes.news)
    g.add_node("advisor", nodes.advisor)
    g.add_node("save_advice", nodes.save_advice)
    g.add_node("notify_user", nodes.notify_user)

    g.add_edge(START, "portfolio_loader")
    # fan-out
    g.add_edge("portfolio_loader", "geo_agent")
    g.add_edge("portfolio_loader", "news_agent")
    # fan-in: advisor waits for both geo and news
    g.add_edge("geo_agent", "advisor")
    g.add_edge("news_agent", "advisor")
    g.add_edge("advisor", "save_advice")
    g.add_edge("save_advice", "notify_user")
    g.add_edge("notify_user", END)
    return g


# --- Scenario graph ------------------------------------------------------------


def build_scenario_graph() -> StateGraph:
    g = StateGraph(ScenarioState)

    g.add_node("scenario_parser", nodes.scenario_parser)
    g.add_node("portfolio_simulator", nodes.portfolio_simulator)
    g.add_node("enrich", nodes.enrich)
    g.add_node("advisor", nodes.advisor)
    g.add_node("compare", nodes.comparison)  # node id != state key 'comparison'

    g.add_edge(START, "scenario_parser")
    g.add_edge("scenario_parser", "portfolio_simulator")
    g.add_edge("portfolio_simulator", "enrich")
    g.add_edge("enrich", "advisor")
    g.add_edge("advisor", "compare")
    g.add_edge("compare", END)
    return g


# --- Compilation ---------------------------------------------------------------

GraphName = str  # "input" | "advisor" | "scenario"


def compile_all(checkpointer: Any) -> dict[GraphName, Any]:
    """Compile every graph against a shared checkpointer.

    The input graph interrupts *before* `human_review` so a human can edit the
    parsed assets and resume the same thread.
    """
    return {
        "input": build_input_graph().compile(
            checkpointer=checkpointer, interrupt_before=["human_review"]
        ),
        "advisor": build_advisor_graph().compile(checkpointer=checkpointer),
        "scenario": build_scenario_graph().compile(checkpointer=checkpointer),
    }
