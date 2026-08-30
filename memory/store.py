"""Persistent memory across sessions.

Plain SQLite rather than a vector DB / mem0: what this agent needs to
remember is structured (user, date, store, item, price), not free-text
semantic recall, so a relational table is both simpler and more
queryable for the "what's changed week over week" questions the
framework calls out.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "basket_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS baskets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    total_cost_dkk REAL NOT NULL,
    budget_dkk REAL NOT NULL,
    stores_used TEXT NOT NULL,       -- JSON list
    plan_json TEXT NOT NULL          -- full PlanOption, for detailed recall
);

CREATE TABLE IF NOT EXISTS staged_orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    store_name TEXT NOT NULL,
    chain TEXT NOT NULL,
    total_cost_dkk REAL NOT NULL,
    item_summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'confirmed' | 'cancelled'
    confirmed_at TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def save_basket(user_id: str, plan: dict, budget_dkk: float) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO baskets (user_id, created_at, total_cost_dkk, budget_dkk, stores_used, plan_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                datetime.now(timezone.utc).isoformat(),
                plan["total_cost_dkk"],
                budget_dkk,
                json.dumps(plan["stores_used"]),
                json.dumps(plan),
            ),
        )
        return cur.lastrowid


def get_history(user_id: str, limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT created_at, total_cost_dkk, budget_dkk, stores_used FROM baskets "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        {
            "created_at": r[0],
            "total_cost_dkk": r[1],
            "budget_dkk": r[2],
            "stores_used": json.loads(r[3]),
        }
        for r in rows
    ]


def week_over_week_delta(user_id: str) -> str | None:
    history = get_history(user_id, limit=2)
    if len(history) < 2:
        return None
    latest, previous = history[0], history[1]
    delta = latest["total_cost_dkk"] - previous["total_cost_dkk"]
    direction = "up" if delta > 0 else "down"
    return f"Your basket total is {direction} {abs(delta):.2f} DKK vs. your last approved plan."


def item_price_last_seen(user_id: str, item_name: str) -> float | None:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT plan_json FROM baskets WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    for (plan_json,) in rows:
        plan = json.loads(plan_json)
        for line in plan.get("lines", []):
            if line["item"] == item_name:
                return line["unit_price_dkk"]
    return None


# --- Mock order staging (used by the natural-language orchestrator) ---
#
# Deliberately split into two steps that don't share a caller: stage_order
# is the only thing the LLM's tool can call, and it never finalizes
# anything. confirm_staged_order is called directly by the UI's "Confirm
# order" button - never by the LLM - so a human always has the final say
# on whether a mock order actually gets recorded, no matter what the LLM
# decides to do. This is the real human-in-the-loop gate for this project:
# not a fixed graph interrupt, but an action the LLM structurally cannot
# perform on its own.

def stage_order(order_id: str, user_id: str, store_name: str, chain: str, total_cost_dkk: float, item_summary: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO staged_orders (order_id, user_id, created_at, store_name, chain, total_cost_dkk, item_summary, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            (order_id, user_id, datetime.now(timezone.utc).isoformat(), store_name, chain, total_cost_dkk, item_summary),
        )


def get_staged_order(order_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT order_id, user_id, created_at, store_name, chain, total_cost_dkk, item_summary, status "
            "FROM staged_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    if not row:
        return None
    keys = ["order_id", "user_id", "created_at", "store_name", "chain", "total_cost_dkk", "item_summary", "status"]
    return dict(zip(keys, row))


def confirm_staged_order(order_id: str) -> bool:
    """Returns True if an order was actually confirmed (existed and was
    still pending), False otherwise - so the caller can tell a stale/
    already-handled order_id apart from a real confirmation."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE staged_orders SET status = 'confirmed', confirmed_at = ? WHERE order_id = ? AND status = 'pending'",
            (datetime.now(timezone.utc).isoformat(), order_id),
        )
        return cur.rowcount > 0


def get_order_history(user_id: str, limit: int = 10) -> list[dict]:
    """Confirmed mock orders for this user, most recent first - what the
    sidebar shows. Only 'confirmed' orders are returned; anything still
    'pending' (shouldn't normally happen with place_mock_order, which
    confirms immediately, but could exist from the older staged flow)
    doesn't show up here."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT order_id, created_at, store_name, chain, total_cost_dkk, item_summary, confirmed_at "
            "FROM staged_orders WHERE user_id = ? AND status = 'confirmed' "
            "ORDER BY confirmed_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    keys = ["order_id", "created_at", "store_name", "chain", "total_cost_dkk", "item_summary", "confirmed_at"]
    return [dict(zip(keys, row)) for row in rows]
