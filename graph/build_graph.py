"""Wires the named agents into a LangGraph state machine.

Architecture (see FRAMEWORK.md for the full write-up and the diagram):

    Intake Agent -> Store Scout Agent -> Pricing Agent -> Optimizer Agent
        -> [HUMAN APPROVAL] -> Export & Memory Agent

The Pamphlet Ingestion Agent is deliberately NOT a node here - it's a
separate, weekly, out-of-band job (scripts/refresh_pamphlets.py) that
writes to a cache the Pricing Agent reads from. Putting a weekly job in
the middle of a live per-request graph would make every user request pay
for (or block on) a PDF download that only needs to happen once a week.

The graph is compiled with a checkpointer and `interrupt_before=["export"]`
so execution genuinely pauses for a human decision - the UI resumes it by
updating state with `approved: True/False` and calling `.invoke(None, ...)`
again on the same thread, rather than the app faking the pause in Python
control flow.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END

from graph.nodes import (
    intake_agent,
    store_scout_agent,
    pricing_agent,
    optimizer_agent,
    human_approval_checkpoint,
    export_memory_agent,
)
from graph.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("intake", intake_agent)
    graph.add_node("store_scout", store_scout_agent)
    graph.add_node("pricing", pricing_agent)
    graph.add_node("optimizer", optimizer_agent)
    graph.add_node("human_approval", human_approval_checkpoint)
    graph.add_node("export", export_memory_agent)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "store_scout")
    graph.add_edge("store_scout", "pricing")
    graph.add_edge("pricing", "optimizer")
    graph.add_edge("optimizer", "human_approval")
    graph.add_edge("human_approval", "export")
    graph.add_edge("export", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["export"])


COMPILED_GRAPH = build_graph()
