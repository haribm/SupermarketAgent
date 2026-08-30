# Week 3 Project — Agent Framework

## The One-Liner

> My agent helps **a grocery shopper in Denmark** do **turning a shopping list and a budget into a
> concrete purchase plan across nearby supermarkets** in **a Streamlit web app**, replacing **the
> manual habit of guessing which store is cheapest or checking 2-3 store apps by hand, which costs
> 15-20 minutes and usually still leaves money on the table**. It does **locating nearby stores,
> pricing every item per store (this week's pamphlet deals, then live REMA 1000, then a
> labeled estimate), and computing the cheapest single-store or split-store plan within
> a distance limit** on its own using **six agents** (Intake, Store Scout, Pricing,
> Optimizer, a weekly Pamphlet Ingestion Agent, and Export/Memory), hands off to the human
> **before the plan is exported/saved, and whenever prices shown are estimated rather than
> real so the user knows what's actually current**, and I'll know it works when **a shopper
> can go from a raw grocery list to an approved, exportable purchase plan in under 2
> minutes**, staying under budget at least **9 times out of 10** for lists where price data
> is available.

## The Framework

| Field | Answer |
|---|---|
| **Agent goal** | Given a grocery list, a budget, and a Danish address, produce the cheapest feasible shopping plan (single store or a split across at most N nearby stores) and let the user approve it before it's saved. |
| **Where do people use it?** | A Streamlit web app (single page): paste a list or upload a CSV/txt, enter address + budget, get a plan. |
| **What steps does it take, in order?** | 1) Parse the list + budget from free text or file. 2) Geocode the address and find nearby supermarkets (real, via OpenStreetMap Overpass). 3) Fetch prices per item per store — real for REMA 1000, clearly-labeled synthetic model for chains with no public price API. 4) Optimize: compute cheapest single store, and cheapest split across ≤2 stores if it saves ≥10%, respecting a max-distance constraint. 5) Present the plan + trade-offs to the human for approval. 6) On approval, export the plan (file) and write the basket to persistent memory. On rejection, let the user adjust budget/distance and re-run step 4. |
| **What can it actually do?** | *Reads (autonomous):* geocode address, find nearby stores, fetch/estimate item prices, compute optimal plan, look up basket history. *Writes (needs approval):* exporting/saving the final plan to a file, writing the basket to memory as "completed." Nothing sends money or places a real order — this system never touches payment or checkout. |
| **What does it need to remember?** | Within a session: the parsed list, address, budget, and candidate stores (session state). Across sessions: past approved baskets and their store/price breakdown, stored in a local SQLite DB, so the agent can answer "how has my grocery bill changed week over week" and "what did I pay for milk last time." |
| **What should it never do?** | Never place a real order or payment. Never invent a "real" price for a chain it can't verify — synthetic prices are always labeled as estimated in the UI. Never silently drop items it couldn't find pricing for — it must list them as unpriced. Never exceed the user's stated budget in the *recommended* plan without flagging it explicitly. |
| **Human-in-the-loop** | After the optimizer proposes a plan, before it is exported or written to memory. The user sees which prices are real vs. estimated, can adjust the max-distance / max-stores constraints, and must click Approve before anything is saved. |
| **What happens when something breaks?** | If the REMA 1000 API call fails or times out: retry twice with backoff, then fall back to the synthetic price model for that item/store and mark it degraded in the UI. If the store locator (Overpass) fails: retry once, then ask the user to enter a postcode/city manually instead of silently failing. If an item in the list can't be matched to any product: it's surfaced to the user as "not priced" rather than guessed. |
| **How do you know it worked?** | A user can paste a real list + budget and get an approved, exported plan in under 2 minutes; for items with real REMA 1000 pricing, the recommended plan stays at or under the stated budget in ≥9/10 test runs. |

## Agent pattern chosen

**A supervised multi-agent pipeline**, not a free-form ReAct loop. Six agents,
each owning one piece of the goal, run in a fixed sequence supervised by an
orchestrator that handles retries and graceful degradation, with one human
checkpoint before the only write action:

1. **Intake Agent** — parses the free-text/file grocery list (LLM, regex fallback)
2. **Store Scout Agent** — finds nearby supermarkets (real tool call)
3. **Pricing Agent** — prices each item, tiered: this week's pamphlet deals (RAG) → live REMA 1000 → labeled estimate
4. **Optimizer Agent** — ranks single-store vs. split-store plans, explains the pick (LLM)
5. **Human Approval** — checkpoint, nothing downstream runs without it
6. **Export & Memory Agent** — writes the plan + basket history

A seventh agent, the **Pamphlet Ingestion Agent**, runs independently on a
weekly schedule (not in the live per-request graph) to keep the pamphlet
RAG cache fresh — see the architecture diagram and README for why it's
kept out-of-band.

This is a deliberate choice per the handout's "creative" prompt: the
control flow (intake → locate → price → optimize → approve → export)
doesn't need to be discovered at runtime the way ReAct discovers it, so
a fixed pipeline of specialized agents is both more reliable and easier
to reason about than a single general-purpose agent looping over tools.
Splitting pricing into pamphlet RAG / live API / estimate tiers, in
particular, only works cleanly as separate concerns *because* the agent
boundaries are fixed rather than dynamically decided.

## Known limitation (stated up front, not hidden)

There is no unified public API for real-time Danish supermarket prices, and I verified this
by checking the real sites rather than assuming: the official Tjek/eTilbudsavis API (which
powers the interactive catalog viewers on netto.dk, foetex.dk, bilka.dk) is private and paid
(requires contacting `services@tjek.com`), and Føtex/Bilka's own sites are JS-rendered
single-page apps with no server-side product data to scrape without a headless browser.

The system uses three tiers, tried in order and always labeled in the UI: (1) a small set of
real, currently-highlighted Netto deals — the one chain of the three whose homepage
genuinely server-renders deal text — via a weekly scrape into a Pinecone vector index with
OpenAI embeddings (bridging English item names to the Danish deal text semantically); (2)
REMA 1000's live product/price endpoint; (3) a clearly-labeled synthetic price model
(per-chain multiplier on a REMA 1000 baseline) for everything else, including all of Føtex
and Bilka's pricing. Every price shown is tagged with which tier produced it. See
README.md's "Why the Pamphlet Ingestion Agent isn't a graph node" section for the full
investigation and what was and wasn't verifiable in this development environment.
