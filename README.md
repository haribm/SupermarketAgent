# Denmark Grocery Budget Agent — Week 3 Project

A chat-based agent that takes a free-text grocery request — items, a
Danish location, optionally a budget, and optionally an instruction to
place an order — reasons about which tools to call, finds nearby
supermarkets, prices the list across real and estimated sources, and,
only when explicitly instructed, places a mock order.

See `FRAMEWORK.md` for the one-liner and the full agent framework.

## Quickstart

```bash
cd grocery-budget-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# OPENAI_API_KEY is required - it powers both the orchestrator's reasoning
# and the pamphlet RAG's embeddings. There's no non-LLM fallback for this.

streamlit run streamlit_app/app.py
```

Then open the local URL Streamlit prints and type a request, e.g.:

> I need milk, bread, and chicken breast near Roskilde, budget 200 DKK

Add "and place the order" to test the mock checkout - it places immediately
when the instruction is explicit, with no separate confirmation step (see
Known Limitations below for why, and the trade-off that implies).

## Agent architecture

A single LLM orchestrator (GPT-5.4, via LangChain's `create_agent`) reads
the message and decides, itself, which of two tools to call and with what
arguments - this is the **single ReAct agent** pattern, not multi-agent.

![Architecture diagram](docs/architecture_diagram.png)

| Component | Role |
|---|---|
| **Orchestrator** (`graph/agentic_orchestrator.py`) | Extracts items, address, budget, and order intent from free text; decides which tool(s) to call |
| **`find_and_price_groceries`** (`tools/agent_tools.py`) | Read-only. Geocodes the address, finds nearby stores, prices every item across three tiers, ranks plans |
| **`place_mock_order`** (`tools/agent_tools.py`) | The one write action - records a confirmed mock order immediately when instructed. No real payment/checkout/delivery system exists behind it |
| **Conversation memory** | A LangGraph checkpointer keyed by browser-session thread id, cached across Streamlit reruns via `st.cache_resource` so multi-turn context actually persists |
| **Order history** | SQLite `staged_orders` table (`memory/store.py`), shown in the sidebar |
| **Pamphlet Ingestion job** (`scripts/refresh_pamphlets.py`) | Weekly, out-of-band: scrapes Netto's real deals, embeds them, indexes in Pinecone - not part of the live request path |

### Pricing tiers, inside `find_and_price_groceries`

I checked the real sites before building this, rather than assuming a PDF or open API would be there:

- The official Tjek/eTilbudsavis API (which powers netto.dk, foetex.dk, bilka.dk's catalog viewers) is **private and paid** — "only available to customers" per tjek.com, requiring a business relationship via `services@tjek.com`. Not obtainable for this project.
- None of the three chains publish a plain downloadable PDF anymore — their weekly catalogs render as interactive Tjek/Incito viewers (JS page images), not files.
- **Føtex and Bilka's own sites are JS-rendered single-page apps** — fetching their offer pages with a plain HTTP request returns placeholder markup (`null varer`, `0,00`); the real product/price data loads client-side after the page runs. Scraping that reliably needs a headless browser, which is more fragility than this project's timeline allows.
- **Netto's homepage is the one exception**: it's server-rendered and includes a small set of real highlighted deals as plain text — verified by fetching it directly and confirming the parser against real DOM structure (see `tools/pamphlet_rag.py`'s `if __name__ == "__main__"` block).

So pricing tries, in order:

1. **This week's real Netto deals** — Pinecone vector search over OpenAI (`text-embedding-3-small`) embeddings of whatever `scripts/refresh_pamphlets.py` last scraped. Tries the item's raw name first (so a brand-specific item like a specific candy can match a pamphlet row named exactly that, with no dependency on the generic catalog below), plus the known Danish term as a hedge.
2. **Live REMA 1000** — an unofficial but publicly reachable product search endpoint.
3. **A labelled estimate model** (`data/catalog.py`) — a REMA 1000 baseline scaled by a per-chain multiplier, used only when nothing real exists. Also the only place the ~25-item generic catalog matters: it supplies the baseline price and expected unit for estimation, nothing more.

A cache miss (item not on deal, no cache yet, or Pinecone/OpenAI not configured) is an ordinary outcome, not an error — pricing falls through to the next tier.

**Pinecone/OpenAI setup**: create free accounts, put both keys in `.env`. The index (`grocery-pamphlets`) is created automatically on first refresh if it doesn't exist. Run `python scripts/refresh_pamphlets.py` to populate it - nothing will match until you do.

**Caveats, stated plainly**:
- The Netto homepage extraction was validated against real HTML fetched from netto.dk, but Netto's markup can change - if `scripts/refresh_pamphlets.py` reports "No deals extracted," re-fetch the page and adjust `NETTO_DEAL_PATTERN` / the DOM traversal in `fetch_netto_homepage_deals` (`tools/pamphlet_rag.py`) the same way.
- `MIN_SCORE = 0.50` in `tools/pamphlet_rag.py` is a starting point for OpenAI embedding cosine similarity, not a measured value - use `debug_pamphlet_match.py` (see below) to check real scores and tune it.
- Coverage is intentionally small (Netto's homepage highlights only, typically 5-10 items in rotation) — don't expect most grocery-list items to hit this tier. That's the honest state of freely-accessible Danish grocery price data, not a bug.

### Store search: Google Maps vs. OpenStreetMap

By default, store search uses free, keyless OpenStreetMap data (Nominatim for geocoding, Overpass for supermarket search). OpenStreetMap's supermarket coverage is volunteer-maintained and can miss real branches - if a real Netto near your address isn't showing up, this is the most likely cause, not a bug.

Setting `GOOGLE_MAPS_API_KEY` in `.env` upgrades this to Google's Geocoding API + **Places API (New)** — generally much better real-world coverage for named chains. If the Google calls fail for any reason (bad key, quota, network), it automatically falls back to OpenStreetMap and labels which backend actually ran.

**Setup**:
1. Create a Google Cloud project and enable billing (required even within the free tier).
2. Enable **"Places API (New)"** and **"Geocoding API"** individually - enabling one does not enable the other. Google froze the legacy Places API in March 2025; it can no longer be newly enabled on a fresh project, so "Places API" (without "(New)") won't work.
3. Create an API key under Credentials, put it in `.env` as `GOOGLE_MAPS_API_KEY`.

## Known limitations (stated up front, not hidden)

**No human approval before placing a mock order.** On the project owner's explicit instruction, `place_mock_order` confirms an order the moment the orchestrator judges the user gave a direct, present-tense instruction to order ("place the order", "order it") rather than a casual description of what they want to buy ("I want to buy milk and bread"). The system prompt (`graph/agentic_orchestrator.py`) is written with explicit matching/non-matching examples to sharpen that distinction, but this is prompt engineering, not a hard guarantee - there is no code-level check on the user's exact wording. This is defensible only because the order is a mock with no real payment/checkout/delivery system behind it; the same design would not be acceptable if this tool were ever pointed at something real.

**No unified public API for real-time Danish supermarket prices.** REMA 1000 has a usable unofficial endpoint; Netto has a small real-time pamphlet via web scraping; Føtex and Bilka have neither, and always fall to the labelled estimate model. Every price in the UI is tagged 🟢 real or 🟡 estimated so nothing is presented as more certain than it is.

**Conversation memory can resurface stale information.** The orchestrator's checkpointer gives it genuine multi-turn memory, but old tool results remain in context indefinitely - during testing it was observed stating a price from several turns earlier as if freshly fetched. The system prompt now explicitly instructs it not to do this, but the store cards (which render directly from a tool's structured JSON output, no LLM involved) remain the authoritative source of truth in any disagreement.

## Testing without live internet

`tools/store_locator.py`, `tools/prices.py`, and `tools/pamphlet_rag.py` all hit real external services and were validated with mocked responses during development (this sandbox's network doesn't reach Overpass, REMA, Google Maps, Pinecone, or OpenAI). Run these locally with real internet access to exercise the live paths:

```bash
python -m tools.store_locator "Norreport, Copenhagen, Denmark"
python -m tools.prices
python -m tools.pamphlet_rag
python scripts/refresh_pamphlets.py
python debug_pamphlet_match.py "chicken breast"   # check real match scores against the cache
```

## Project layout

```
graph/agentic_orchestrator.py   the orchestrator: model, tools, system prompt
tools/agent_tools.py            the two tools exposed to the orchestrator
tools/                          store locator, price lookup, optimizer, pamphlet RAG
memory/store.py                 SQLite: basket history + staged/confirmed mock orders
data/catalog.py                 baseline prices + chain multipliers (the estimate model)
streamlit_app/app.py            the chat UI
scripts/refresh_pamphlets.py    weekly pamphlet ingestion job
debug_pamphlet_match.py         checks real pamphlet match scores for a given item
docs/architecture_diagram.png   the diagram embedded above
FRAMEWORK.md                    the filled-in agent framework
```
