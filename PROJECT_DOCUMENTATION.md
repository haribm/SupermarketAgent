# Week 3 Project Documentation — Denmark Grocery Budget Agent

*(Copy this into the Google Doc for submission.)*

## Project overview

A grocery shopper enters a list, a budget, and a Danish address into a
Streamlit app. The agent finds nearby supermarkets, prices each item at
each store, and recommends the cheapest plan — either one store or a
split across up to two — under a distance limit the shopper sets. The
shopper reviews and must approve the plan before it's exported to a file
and logged to a persistent basket-history database.

Built as a LangGraph pipeline (parse → locate → price → optimize →
**human approval** → export), not a free-form ReAct loop, because the
control flow is fixed and mostly deterministic — the two places an LLM
actually helps are parsing messy free-text lists and writing the
plain-English plan explanation. Full framework in `FRAMEWORK.md`.

## Datasets / data sources used

1. **Store locations** — real, live: OpenStreetMap Nominatim (geocoding)
   + Overpass API (`shop=supermarket` search), both free and keyless.
2. **REMA 1000 prices** — attempted live via REMA 1000's unofficial
   product-search endpoint; falls back to (3) below on any failure.
3. **Baseline/estimated prices for all other chains** — a hand-built
   catalog (`data/catalog.py`) of ~25 common grocery items at REMA
   1000-tier prices, scaled by a per-chain multiplier sourced from
   published Danish cost-of-living/basket comparisons (Netto ≈ REMA,
   Foetex/Kvickly/Bilka ≈ 8–18% higher, Irma ≈ 35% higher, discounters
   like Lidl/Aldi ≈ 2–3% lower). **This is explicitly labeled "estimated"
   everywhere it surfaces** — see the Known Limitation section of
   `FRAMEWORK.md`.

There is no single official, unified API for real-time grocery prices
across Danish supermarket chains. This was verified by research before
committing to the architecture, rather than discovered midway through
the build.

## Prompts used during vibe-coding (representative)

- "Design a multi-step LangGraph pipeline for a grocery budget agent
  with a real human-approval interrupt before any write action — walk
  through the state schema first."
- "Write a store-locator tool using free, keyless services (no Google
  API key) that geocodes a Danish address and finds nearby supermarkets."
- "The regex list parser is mis-parsing 'a dozen eggs' and '~500g chicken
  breast' — trace through why and fix the quantity/unit extraction."
- "The optimizer's split-store option isn't showing up when I'd expect
  it to — is that a bug or a property of the flat per-chain multiplier
  model?" (See Learnings below — turned out to be the latter.)

## Iterations tried

- **Price data source**: first considered scraping 4–5 chains' websites
  directly. Rejected for a one-week project — too fragile, and it would
  have hidden the "we don't have real data for most chains" problem
  instead of designing for it. Settled on real-where-possible +
  labeled-estimate-elsewhere.
- **Memory layer**: considered mem0 for basket history. Switched to
  plain SQLite because the actual need (structured week-over-week price
  history) is relational, not semantic-recall — a simpler tool that
  fits the problem better.
- **Quantity handling**: initial version multiplied raw user quantity by
  unit price without unit conversion (e.g. "500g chicken" was priced as
  500× the per-kg price). Caught in testing; added `convert_quantity()`
  to normalize user units (g/ml) to the catalog's pricing unit (kg/l)
  before computing line totals.
- **Regex parser**: initial pattern didn't handle "a dozen eggs" (word
  quantities) or a leading "~" (approx.) before a number. Added a
  separate word-quantity pattern and stripped approximation markers.

## Learnings / observations

- The split-across-stores optimizer strategy only pays off when prices
  vary *by item*, not just by a flat per-store multiplier. With the
  synthetic estimate model, a store's multiplier applies uniformly to
  every item, so one store is always cheapest for the whole basket and
  the split option correctly never triggers. Real per-item price
  variation (which the live REMA 1000 data would supply, and which real
  competing chains actually have via weekly `tilbud`/offers) is exactly
  what would make the split-store recommendation useful in practice —
  a good illustration of why "state the hard part" in the framework
  matters: the estimate model is honest about its own limits by *not*
  producing artificially interesting splits.
- Retrying-then-degrading-then-labeling (rather than retry-then-crash or
  retry-then-silently-guess) turned out to be reusable across two
  unrelated tools (store locator and price fetch) — worth designing as
  a shared pattern rather than one-off error handling per tool.
- Separating "read" tools (locate, price, optimize — all autonomous) from
  the one "write" step (export) made the human-in-the-loop interrupt
  trivial to place correctly: it's the only edge in the graph that leads
  to a state-mutating node.
