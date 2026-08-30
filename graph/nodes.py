"""Agent implementations for the grocery-budget multi-agent system.

Each function is one named agent in the architecture (see FRAMEWORK.md):
Intake Agent, Store Scout Agent, Pricing Agent, Optimizer Agent, the
Human Approval checkpoint, and the Export & Memory Agent. They're plain
functions rather than classes because LangGraph nodes are just
state -> partial-state-update callables - wrapping them in an "Agent"
class would add ceremony without adding capability here. What makes each
one an "agent" rather than a bare function is that it owns a distinct
piece of the goal (find stores / price items / pick a plan / persist
history) and, for Intake and Optimizer's explanation, makes its own LLM
call rather than following a hand-coded script.

The weekly Pamphlet Ingestion Agent (tools/pamphlet_rag.py, invoked by
scripts/refresh_pamphlets.py) intentionally is NOT a node in this graph -
it runs on its own schedule, out of the synchronous request path, and the
Pricing Agent below just reads whatever it last cached.
"""
from __future__ import annotations

import os

from data.catalog import normalize_item_name, BASELINE_DKK, convert_quantity, get_logo_url
from graph.state import AgentState, PricedItem
from memory.store import save_basket, week_over_week_delta
from tools.optimizer import build_plan_options
from tools.parser import parse_grocery_list
from tools.prices import price_item_at_store
from tools.store_locator import locate_stores


def intake_agent(state: AgentState) -> dict:
    text = state.get("raw_list_text", "") or ""
    file_text = state.get("uploaded_file_text") or ""
    combined = "\n".join(t for t in [text, file_text] if t)
    items = parse_grocery_list(combined)
    errors = [] if items else ["No items could be parsed from the input."]
    return {"items": items, "parse_errors": errors, "status": "parsed"}


def store_scout_agent(state: AgentState) -> dict:
    address = state["address"]
    result = locate_stores(address, radius_m=int(state.get("max_distance_km", 3) * 1000), limit=20)
    stores = result["stores"]
    degraded = bool(result.get("error"))
    errors = state.get("errors", [])
    if result.get("error"):
        errors = errors + [result["error"]]
    return {
        "stores": stores,
        "locate_degraded": degraded,
        "locate_error": result.get("error"),
        "location_source": result.get("source"),
        "errors": errors,
        "status": "stores_located" if stores else "locate_failed",
    }


def pricing_agent(state: AgentState) -> dict:
    items = state.get("items", [])
    stores = state.get("stores", [])
    priced_lines: list[PricedItem] = []
    degraded_chains: set[str] = set()
    unpriced: list[str] = []

    if not stores:
        return {
            "priced_lines": [],
            "price_degraded_chains": [],
            "unpriced_items": [i["name"] for i in items],
            "status": "no_stores_to_price",
        }

    # Price every store within the user's distance limit. `locate_stores`
    # already caps how many candidates exist (see store_scout_agent), so
    # this isn't unbounded - just no longer silently dropping stores past
    # the 6th-nearest, which was hiding real stores like Netto from the
    # results even when they were within range.
    candidate_stores = stores

    for item in items:
        canonical = normalize_item_name(item["name"])
        any_priced = False
        for store in candidate_stores:
            result = price_item_at_store(item["name"], store["chain"])
            if result["unit_price_dkk"] is None:
                continue
            any_priced = True
            if result["source"] == "estimated":
                degraded_chains.add(store["chain"])
            qty = item.get("quantity") or 1.0
            # convert the user's quantity/unit into whatever unit the price is quoted in
            catalog_unit = BASELINE_DKK[canonical][0] if canonical in BASELINE_DKK else None
            priced_qty = convert_quantity(qty, item.get("unit"), catalog_unit) if catalog_unit else qty
            priced_lines.append(
                {
                    "item": item["name"],
                    "store": store["name"],
                    "chain": store["chain"],
                    "unit_price_dkk": result["unit_price_dkk"],
                    "line_total_dkk": round(result["unit_price_dkk"] * priced_qty, 2),
                    "source": result["source"],
                    "source_detail": result.get("source_detail", ""),
                    "source_url": result.get("source_url"),
                    "matched_product": result["matched_product"],
                }
            )
        if not any_priced:
            unpriced.append(item["name"])

    return {
        "priced_lines": priced_lines,
        "price_degraded_chains": sorted(degraded_chains),
        "unpriced_items": unpriced,
        "status": "priced",
    }


def optimizer_agent(state: AgentState) -> dict:
    priced_lines = state.get("priced_lines", [])
    stores = state.get("stores", [])
    store_distance = {s["name"]: s["distance_km"] for s in stores}

    priced_by_item: dict[str, list[PricedItem]] = {}
    for line in priced_lines:
        priced_by_item.setdefault(line["item"], []).append(line)
    for item in state.get("items", []):
        priced_by_item.setdefault(item["name"], [])

    options, unpriced = build_plan_options(
        priced_by_item,
        budget_dkk=state["budget_dkk"],
        max_distance_km=state.get("max_distance_km", 3.0),
        max_stores=state.get("max_stores", 2),
        store_distance=store_distance,
    )

    recommended = options[0] if options else None
    explanation = _explain_plan(recommended, state.get("budget_dkk", 0), state.get("price_degraded_chains", []))
    store_summaries = _build_store_summaries(state, priced_lines)

    return {
        "plan_options": options,
        "recommended_plan": recommended,
        "store_summaries": store_summaries,
        "unpriced_items": unpriced,
        "explanation": explanation,
        "status": "optimized" if recommended else "no_plan_possible",
    }


def _build_store_summaries(state: AgentState, priced_lines: list[PricedItem]) -> list:
    """One entry per nearby store within the user's distance limit, with
    its total for whatever items were priceable there - used by the UI to
    show every candidate store side by side, not just the winning plan.
    """
    all_item_names = {item["name"] for item in state.get("items", [])}
    max_distance = state.get("max_distance_km", 3.0)

    lines_by_store: dict[str, list[PricedItem]] = {}
    for line in priced_lines:
        lines_by_store.setdefault(line["store"], []).append(line)

    summaries = []
    for store in state.get("stores", []):
        if store["distance_km"] > max_distance:
            continue
        store_lines = lines_by_store.get(store["name"], [])
        priced_item_names = {l["item"] for l in store_lines}
        summaries.append(
            {
                "store": store["name"],
                "chain": store["chain"],
                "distance_km": store["distance_km"],
                "logo_url": get_logo_url(store["chain"]),
                "total_cost_dkk": round(sum(l["line_total_dkk"] for l in store_lines), 2),
                "priced_item_count": len(priced_item_names),
                "unpriced_items": sorted(all_item_names - priced_item_names),
                "lines": store_lines,
            }
        )
    summaries.sort(key=lambda s: (0, s["total_cost_dkk"]) if s["priced_item_count"] > 0 else (1, s["distance_km"]))
    return summaries


def _explain_plan(plan, budget_dkk: float, degraded_chains: list[str]) -> str:
    if plan is None:
        return "No plan could be built - none of the items could be priced at a nearby store."
    lines = [
        f"Recommended: {plan['label']} - total {plan['total_cost_dkk']:.2f} DKK "
        f"(budget: {budget_dkk:.2f} DKK)."
    ]
    if plan["over_budget"]:
        lines.append(f"This is over budget by {plan['total_cost_dkk'] - budget_dkk:.2f} DKK.")
    else:
        lines.append(f"That's {budget_dkk - plan['total_cost_dkk']:.2f} DKK under budget.")
    if plan["savings_vs_baseline_pct"] > 0:
        lines.append(f"Saves {plan['savings_vs_baseline_pct']:.1f}% vs. the baseline single-store cost.")
    if degraded_chains:
        lines.append(
            "Some line items are estimated rather than live-priced (see the tags on each line below) - "
            "either no pamphlet deal or live REMA 1000 price was available for that item."
        )
    if plan["unpriced_items"]:
        lines.append(f"Could not price: {', '.join(plan['unpriced_items'])} - add these manually.")
    return " ".join(lines)


def human_approval_checkpoint(state: AgentState) -> dict:
    """No-op pass-through node used purely as the graph's interrupt point.

    The actual approve/reject decision is written into state by the
    Streamlit UI (via graph.update_state) before the graph is resumed -
    see streamlit_app/app.py. This mirrors the framework's requirement
    that any write action (export, save) sits behind an explicit human
    checkpoint rather than the agent deciding on its own.
    """
    return {"status": "awaiting_approval"}


def export_memory_agent(state: AgentState) -> dict:
    if not state.get("approved"):
        return {"status": "rejected", "exported_path": None}

    plan = state["recommended_plan"]
    user_id = state.get("user_id", "anonymous")

    save_basket(user_id, plan, state["budget_dkk"])
    delta_note = week_over_week_delta(user_id)

    export_dir = os.path.join(os.path.dirname(__file__), "..", "exports")
    os.makedirs(export_dir, exist_ok=True)
    path = os.path.join(export_dir, f"plan_{user_id}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Grocery plan for {user_id}\n")
        f.write(f"{plan['label']}\n")
        f.write(f"Total: {plan['total_cost_dkk']:.2f} DKK (budget {state['budget_dkk']:.2f} DKK)\n\n")
        for line in plan["lines"]:
            tag = "" if line["source"] == "real" else " (estimated)"
            f.write(f"- {line['item']}: {line['unit_price_dkk']:.2f} DKK at {line['store']}{tag}\n")

    return {
        "status": "exported",
        "exported_path": path,
        "history_note": delta_note,
    }
