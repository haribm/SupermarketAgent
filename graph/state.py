"""Shared state for the grocery budget planning graph.

Kept as a plain TypedDict (not pydantic) because LangGraph nodes only ever
add/replace keys - there's no validation logic worth paying pydantic's
overhead for here.
"""
from __future__ import annotations

from typing import TypedDict, Literal, Optional


class GroceryItem(TypedDict):
    name: str              # normalized name, e.g. "milk"
    raw_text: str           # what the user actually typed, e.g. "2L whole milk"
    quantity: float          # numeric quantity if parseable, else 1.0
    unit: Optional[str]      # "l", "kg", "pcs", etc. best-effort


class StoreCandidate(TypedDict):
    name: str
    chain: str               # "REMA 1000" | "Netto" | "Foetex" | "Bilka" | ...
    lat: float
    lon: float
    distance_km: float
    address: Optional[str]


class PricedItem(TypedDict):
    item: str
    store: str
    chain: str
    unit_price_dkk: float
    line_total_dkk: float
    source: Literal["real", "estimated"]
    source_detail: str  # "pamphlet" | "pamphlet_stale" | "rema_live" | "baseline_multiplier" | "no_match"
    source_url: Optional[str]  # link to where the price came from, if any real source exists
    matched_product: Optional[str]


class StoreSummary(TypedDict):
    store: str
    chain: str
    distance_km: float
    logo_url: Optional[str]
    total_cost_dkk: float          # sum of priced items only
    priced_item_count: int
    unpriced_items: list[str]
    lines: list[PricedItem]


class PlanOption(TypedDict):
    label: str                 # "Single store: Netto Norrebro"
    stores_used: list[str]
    total_cost_dkk: float
    unpriced_items: list[str]
    lines: list[PricedItem]
    over_budget: bool
    savings_vs_baseline_pct: float


class AgentState(TypedDict, total=False):
    # --- input ---
    raw_list_text: str
    uploaded_file_text: Optional[str]
    address: str
    budget_dkk: float
    max_distance_km: float
    max_stores: int
    user_id: str

    # --- parse ---
    items: list[GroceryItem]
    parse_errors: list[str]

    # --- locate ---
    stores: list[StoreCandidate]
    locate_degraded: bool
    locate_error: Optional[str]
    location_source: str  # "google" | "openstreetmap"

    # --- price ---
    priced_lines: list[PricedItem]
    price_degraded_chains: list[str]   # chains where we fell back to estimates
    unpriced_items: list[str]

    # --- optimize ---
    plan_options: list[PlanOption]
    recommended_plan: Optional[PlanOption]
    store_summaries: list[StoreSummary]
    explanation: str

    # --- human-in-the-loop ---
    approved: Optional[bool]
    user_adjustments: Optional[dict]

    # --- memory / export ---
    exported_path: Optional[str]
    history_note: Optional[str]

    # --- control ---
    errors: list[str]
    status: str
