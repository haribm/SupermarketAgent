"""Canonical grocery catalog.

`BASELINE_DKK` are REMA 1000-tier discount prices per canonical unit, sourced
from published Danish cost-of-living comparisons (see FRAMEWORK.md for the
caveat about no unified live price API). These act as:
  1. The fallback price when the live REMA 1000 lookup fails or the SKU
     can't be matched exactly.
  2. The base that `CHAIN_MULTIPLIERS` scales up for chains that don't
     expose a public price API at all (Netto, Foetex, Bilka, ...).

Every price produced from this file is tagged "estimated" downstream -
never "real" - so the UI is honest about what it's showing.
"""

# canonical_name -> (unit, price_dkk_per_unit)
BASELINE_DKK: dict[str, tuple[str, float]] = {
    "milk": ("l", 8.5),
    "bread": ("pcs", 15.0),
    "eggs": ("dozen", 28.0),
    "chicken breast": ("kg", 55.0),
    "minced beef": ("kg", 70.0),
    "rice": ("kg", 14.0),
    "pasta": ("kg", 12.0),
    "potatoes": ("kg", 8.0),
    "onion": ("kg", 10.0),
    "tomato": ("kg", 22.0),
    "cucumber": ("pcs", 7.0),
    "apple": ("kg", 18.0),
    "banana": ("kg", 14.0),
    "butter": ("pcs", 22.0),
    "cheese": ("kg", 85.0),
    "yogurt": ("l", 20.0),
    "coffee": ("pcs", 45.0),
    "olive oil": ("l", 55.0),
    "sugar": ("kg", 11.0),
    "flour": ("kg", 9.0),
    "salmon": ("kg", 130.0),
    "carrot": ("kg", 9.0),
    "orange juice": ("l", 16.0),
    "toilet paper": ("pack", 35.0),
    "dish soap": ("pcs", 20.0),
}

# Multiplier relative to REMA 1000 baseline, from published Danish
# cost-of-living / basket comparisons. These are estimates, not live prices.
CHAIN_MULTIPLIERS: dict[str, float] = {
    "REMA 1000": 1.00,   # real prices fetched live where possible
    "Netto": 1.02,
    "SuperBrugsen": 1.10,
    "Foetex": 1.18,
    "Kvickly": 1.12,
    "Bilka": 1.08,
    "Irma": 1.35,
    "Lidl": 0.98,
    "Aldi": 0.97,
    "Nemlig": 1.20,
}

DEFAULT_MULTIPLIER = 1.15  # unknown/unlisted chain -> assume mid-tier premium

# Domain per chain, used to fetch a logo via Clearbit's free public logo API
# (https://logo.clearbit.com/{domain}) - no key required. This is a
# third-party service the app doesn't control; if a logo fails to load,
# the UI falls back to a text label rather than breaking (see
# streamlit_app/app.py).
CHAIN_LOGO_DOMAINS: dict[str, str] = {
    "REMA 1000": "rema1000.dk",
    "Netto": "netto.dk",
    "Foetex": "foetex.dk",
    "Bilka": "bilka.dk",
    "Kvickly": "kvickly.dk",
    "SuperBrugsen": "superbrugsen.dk",
    "Irma": "irma.dk",
    "Lidl": "lidl.dk",
    "Nemlig": "nemlig.com",
    "Aldi": "aldi.dk",
}


def get_logo_url(chain: str) -> str | None:
    domain = CHAIN_LOGO_DOMAINS.get(chain)
    return f"https://logo.clearbit.com/{domain}" if domain else None


def get_logo_fallback_url(chain: str) -> str | None:
    """Google's favicon service as a fallback when Clearbit doesn't have a
    logo for a domain (common for smaller/regional retailers) - lower
    resolution but far more universally populated. Used client-side via an
    <img onerror> chain in the Streamlit UI, not called directly here."""
    domain = CHAIN_LOGO_DOMAINS.get(chain)
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64" if domain else None

ALIASES: dict[str, str] = {
    "whole milk": "milk", "skimmed milk": "milk", "semi milk": "milk",
    "loaf of bread": "bread", "white bread": "bread", "rye bread": "bread",
    "egg": "eggs",
    "chicken": "chicken breast", "chicken fillet": "chicken breast",
    "beef mince": "minced beef", "ground beef": "minced beef",
    "onions": "onion", "tomatoes": "tomato", "cucumbers": "cucumber",
    "apples": "apple", "bananas": "banana", "carrots": "carrot",
    "oj": "orange juice", "juice": "orange juice",
    "toiletpaper": "toilet paper", "loo roll": "toilet paper",
}

# Danish search terms for pamphlet (tilbudsavis) lookup - pamphlets are
# published in Danish, so an English canonical name like "chicken breast"
# has zero token overlap with a Danish pamphlet row like "kyllingebryst
# 500g" under BM25. Without this bridge, pamphlet retrieval would silently
# miss on every query and the RAG tier would never actually fire.
DANISH_QUERY_TERMS: dict[str, str] = {
    "milk": "mælk",
    "bread": "brød",
    "eggs": "æg",
    "chicken breast": "kyllingebryst",
    "minced beef": "hakket oksekød",
    "rice": "ris",
    "pasta": "pasta",
    "potatoes": "kartofler",
    "onion": "løg",
    "tomato": "tomat",
    "cucumber": "agurk",
    "apple": "æble",
    "banana": "banan",
    "butter": "smør",
    "cheese": "ost",
    "yogurt": "yoghurt",
    "coffee": "kaffe",
    "olive oil": "olivenolie",
    "sugar": "sukker",
    "flour": "mel",
    "salmon": "laks",
    "carrot": "gulerod",
    "orange juice": "appelsinjuice",
    "toilet paper": "toiletpapir",
    "dish soap": "opvaskemiddel",
}


# Conversion factors to the "base" unit for each unit family.
# e.g. 500 g -> 0.5 kg because kg is BASELINE_DKK's unit for weight items.
_UNIT_TO_BASE = {
    "g": ("kg", 0.001), "kg": ("kg", 1.0),
    "ml": ("l", 0.001), "l": ("l", 1.0),
    "dozen": ("dozen", 1.0), "pcs": ("pcs", 1.0), "pack": ("pack", 1.0),
}


def convert_quantity(quantity: float, from_unit: str | None, to_unit: str) -> float:
    """Convert `quantity` in `from_unit` into an equivalent amount in `to_unit`.

    Falls back to treating the quantity as already-in-target-unit (i.e. a
    no-op) whenever the units are unknown or incompatible - pricing on a
    slightly-off quantity beats crashing or silently dropping the item.
    """
    if not from_unit or from_unit == to_unit:
        return quantity
    src = _UNIT_TO_BASE.get(from_unit)
    dst_family = _UNIT_TO_BASE.get(to_unit)
    if src is None or dst_family is None or src[0] != dst_family[0]:
        return quantity
    base_amount = quantity * src[1]
    return base_amount / dst_family[1]


def normalize_item_name(raw: str) -> str | None:
    """Best-effort mapping of a free-text item to a canonical catalog key."""
    cleaned = raw.strip().lower()
    if cleaned in BASELINE_DKK:
        return cleaned
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    # loose contains-match as a last resort
    for key in BASELINE_DKK:
        if key in cleaned or cleaned in key:
            return key
    for alias, key in ALIASES.items():
        if alias in cleaned:
            return key
    return None
