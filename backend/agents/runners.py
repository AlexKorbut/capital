"""Thin helpers to invoke the compiled graphs from routers.

A `GraphRegistry` (the dict returned by `graph.compile_all`) is created once at
app startup and stored on `app.state.graphs`. These helpers wrap the LangGraph
`ainvoke`/`aget_state` calls with the per-user thread config.
"""
from __future__ import annotations

from typing import Any

GraphRegistry = dict[str, Any]


def _thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


async def run_input(
    graphs: GraphRegistry, thread_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Run the input pipeline. If it pauses at `human_review`, the returned
    state will reflect the pre-interrupt values; inspect with `get_pending`.
    """
    return await graphs["input"].ainvoke(state, _thread_config(thread_id))


async def resume_input(
    graphs: GraphRegistry, thread_id: str, edits: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Resume an input thread paused at `human_review`, optionally applying
    human edits (e.g. corrected `assets`) before continuing.
    """
    cfg = _thread_config(thread_id)
    if edits:
        await graphs["input"].aupdate_state(cfg, edits)
    return await graphs["input"].ainvoke(None, cfg)


async def get_pending(graphs: GraphRegistry, thread_id: str) -> Any:
    """Return the current persisted state snapshot for a thread (or None)."""
    return await graphs["input"].aget_state(_thread_config(thread_id))


async def run_advisor(
    graphs: GraphRegistry, thread_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    return await graphs["advisor"].ainvoke(state, _thread_config(thread_id))


async def run_scenario(
    graphs: GraphRegistry, thread_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    return await graphs["scenario"].ainvoke(state, _thread_config(thread_id))
