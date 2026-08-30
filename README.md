# Denmark Grocery Budget Agent — Week 3 Project

An agentic system that takes a grocery list, a budget, and a Danish address,
finds nearby supermarkets, prices the list, and proposes the cheapest
shopping plan — pausing for human approval before anything is saved.

See `FRAMEWORK.md` for the one-liner, the full agent framework, and the
explicit, stated limitation around Danish grocery price data.

## Quickstart

```bash
cd grocery-budget-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# optional: add ANTHROPIC_API_KEY to .env for LLM-based list parsing +
# explanations. Without it, the app still runs end-to-end using a
# regex-based parser and a template explanation.

streamlit run streamlit_app/app.py
```

Then open the local URL Streamlit prints, enter a grocery list + a real
Danish address (e.g. "Norreport, Copenhagen" or a postcode), a budget, and
click **Build my plan**.

## Agent architecture

**Five named agents in a supervised pipeline, plus one background agent on its own weekly schedule.**

| # | Agent | Role | LLM reasoning? |
|---|---|---|---|
| 1 | **Intake Agent** | Free-text/file grocery list → structured items | Yes (falls back to regex without an API key) |
| 2 | **Store Scout Agent** | Finds nearby supermarkets | No — real tool call (Google Maps if configured, else OpenStreetMap) |
| 3 | **Pricing Agent** | Prices each item at each store, tiered: pamphlet RAG → live REMA 1000 → estimate | No — deterministic tiered lookup |
| 4 | **Optimizer Agent** | Ranks single-store vs. split-store plans, writes the plan explanation | Yes, for the explanation only |
| 5 | **Human Approval** | Checkpoint — nothing downstream runs without it | — (a pause, not an agent) |
| 6 | **Export & Memory Agent** | Writes the plan to a file + SQLite basket history | No |
| — | **Pamphlet Ingestion Agent** *(background, weekly)* | Downloads chain pamphlets, extracts deal prices, refreshes the RAG cache | No — runs out of band |

An **Orchestrator** supervises agents 1–4 (sequencing, retries, graceful degradation on tool failure) before the human checkpoint. `graph/build_graph.py` compiles this as a LangGraph `StateGraph` with `interrupt_before=["export"]`, so the graph genuinely pauses for approval — the Streamlit app resumes it by writing `approved: True/False` into graph state and re-invoking, not by faking the pause in Python.

| Graph node | Agent | Tool(s) |
|---|---|---|
| `intake` | Intake Agent | `tools/parser.py` |
| `store_scout` | Store Scout Agent | `tools/store_locator.py` (Google Maps if `GOOGLE_MAPS_API_KEY` set, else Nominatim + Overpass) |
| `pricing` | Pricing Agent | `tools/pamphlet_rag.py` → `tools/prices.py` |
| `optimizer` | Optimizer Agent | `tools/optimizer.py` |
| `human_approval` | (checkpoint) | interrupt, resumed by the UI |
| `export` | Export & Memory Agent | `memory/store.py` |

### Why the Pamphlet Ingestion Agent isn't a graph node, and what it actually covers

I checked the real sites before building this, rather than assuming a PDF would be there:

- The official Tjek/eTilbudsavis API (which powers netto.dk, foetex.dk, bilka.dk's catalog viewers) is **private and paid** — "only available to customers" per tjek.com, requiring a business relationship via `services@tjek.com`. Not obtainable for this project.
- None of the three chains publish a plain downloadable PDF anymore — their weekly catalogs render as interactive Tjek/Incito viewers (JS page images), not files.
- **Føtex and Bilka's own sites are JS-rendered single-page apps** — fetching their offer pages with a plain HTTP request returns placeholder markup (`null varer`, `0,00`); the real product/price data loads client-side after the page runs. Scraping that reliably needs a headless browser, which is more fragility than this project's timeline allows.
- **Netto's homepage is the one exception**: it's server-rendered and includes a small set of real highlighted deals as plain text (product name, price, days-until-expiry) — verified by fetching it directly and confirming the parser against that real text (see `tools/pamphlet_rag.py`'s `if __name__ == "__main__"` block).

So the pamphlet tier covers **a small number of real Netto deals**, not a full weekly catalog across all three chains. Føtex and Bilka stay on the labeled estimate model — an accurate reflection of what's actually accessible without a paid API or a headless-browser scraper, not a shortcut I'm hiding.

- `scripts/refresh_pamphlets.py` runs on a schedule (cron/Task Scheduler), scrapes Netto's homepage, embeds each deal's item name, and upserts into a **Pinecone** index (`tools/pamphlet_rag.py`).
- The Pricing Agent queries Pinecone first (tier 1, Netto only), then the live REMA 1000 API (tier 2), then the labeled estimate model (tier 3, all chains).
- Retrieval uses **semantic vector search**: Netto's deals are in Danish ("løgismose hel kylling") and grocery lists are typically English ("chicken"). Embeddings come from OpenAI's `text-embedding-3-small` (`OPENAI_API_KEY` required alongside `PINECONE_API_KEY`). As a hedge in case cross-lingual retrieval underperforms in practice, the Pricing Agent also tries the known Danish term from `DANISH_QUERY_TERMS` (`data/catalog.py`) and keeps whichever query scores higher.
- A cache miss (item not in this week's highlights, no cache yet, or Pinecone/OpenAI not configured) is an ordinary outcome, not an error — pricing falls through to the next tier, same retry-then-degrade-then-label pattern used everywhere else.

**Setup**: create a free Pinecone account and an OpenAI account, put both keys in `.env`. The index is created automatically on first refresh if it doesn't exist.

**Caveats, stated plainly**:
- The Netto homepage regex (`NETTO_DEAL_PATTERN`) was validated against real text fetched from netto.dk on 2026-08-30 — if Netto changes their homepage copy or markup, re-fetch the page and adjust the pattern the same way (fetch, inspect the flattened text, update the regex).
- This sandbox can't reach `api.pinecone.io` or `api.openai.com`, so the Pinecone/embedding calls were verified with a fake in-memory index — it confirms the surrounding logic (chain filtering, staleness, dual-query fallback) is correct, not that real semantic matching quality is good enough. `MIN_SCORE = 0.50` is a starting point for OpenAI embeddings, not a measured value — **tune it against real query results before recording the demo**.
- Coverage is intentionally small (Netto's homepage highlights only, maybe 5-10 items in rotation at any time) — don't expect most grocery-list items to hit this tier. That's the honest state of freely-accessible Danish grocery price data, not a bug.

### Store Scout Agent: Google Maps vs. OpenStreetMap

By default, the Store Scout Agent uses free, keyless OpenStreetMap data (Nominatim for geocoding, Overpass for supermarket search). This is genuinely free real data, but OpenStreetMap's supermarket coverage is volunteer-maintained and can miss specific branches — if a real Netto near your address isn't showing up, this is the most likely cause, not a bug in the pipeline.

Setting `GOOGLE_MAPS_API_KEY` in `.env` upgrades this to Google's Geocoding API + **Places API (New)** — generally much better real-world POI coverage for named chains. If the Google calls fail for any reason (bad key, quota, network), the agent automatically falls back to OpenStreetMap rather than failing the whole request.

**Setup**:
1. Create a Google Cloud project and enable billing (Google requires a billing account even within the free tier — usage this small won't incur charges, but the option to enable it must be there).
2. Enable **"Places API (New)"** and **"Geocoding API"** — specifically the *(New)* Places API. Google froze the legacy Places API in March 2025 and it can no longer be newly enabled on a fresh project, so "Places API" (without "(New)") won't work if you're setting this up now.
3. Create an API key under Credentials, and put it in `.env` as `GOOGLE_MAPS_API_KEY`.

**Caveat, stated plainly**: this sandbox can't reach `maps.googleapis.com` or `places.googleapis.com`, so the Google-backed path in `tools/store_locator.py` was verified with mocked responses (chain classification, distance sorting, and the fallback-to-OpenStreetMap-on-failure logic all check out), not a real API call. Run `python -m tools.store_locator "your address"` once you have a real key to confirm the live request format matches what Google's current API expects.

## Known limitation (by design, not an oversight)

There's no unified public API for real-time Danish supermarket prices.
REMA 1000 has a usable (unofficial) product/price endpoint; other chains
don't expose one. The app uses real REMA 1000 prices where reachable and a
clearly-labeled synthetic estimate (per-chain multiplier on a REMA 1000
baseline) everywhere else — every price in the UI is tagged 🟢 real or
🟡 estimated so nothing is presented as more accurate than it is.

## Testing without live internet

`tools/store_locator.py` and `tools/prices.py` both hit real external
services and were validated with mocked responses during development (this
sandbox's network doesn't reach `overpass-api.de` or the REMA 1000 domain).
Run these locally with real internet access to exercise the live paths:

```bash
python -m tools.store_locator "Norreport, Copenhagen, Denmark"
python -m tools.prices
```

## Project layout

```
graph/            LangGraph state + nodes + graph assembly
tools/            parser, store locator, price lookup, optimizer
memory/           SQLite basket-history store
data/catalog.py   baseline prices + chain multipliers (the estimate model)
streamlit_app/    the UI
FRAMEWORK.md      the filled-in agent framework (submission doc source)
```
