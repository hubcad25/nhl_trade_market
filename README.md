# NHL Trade Market Predictor

## Objective

Given a player's profile (stats, contract, qualitative context), predict the trade package that player would fetch on the NHL trade market — expressed as a structured JSON output containing picks, prospects, and roster players.

The model is a small fine-tuned LLM (Mistral 7B or Llama 3 8B) trained on (input prompt, output JSON) pairs built from enriched historical NHL trades.

---

## Trade Element Types

Each side of a trade is decomposed into typed elements, each with its own enrichment schema:

| Type | Count | Key Sources |
|---|---|---|
| NHL Skater | 480 | NHL API, MoneyPuck, PuckPedia, web articles (Tavily) |
| Skater Prospect | 192 | EliteProspects, web articles (Tavily) |
| NHL Goalie | 45 | NHL API, MoneyPuck (GSAx), PuckPedia, web articles |
| Goalie Prospect | 10 | EliteProspects, web articles |
| Pick | 456 | Tier estimation (NHL standings at trade date), original team, year |
| Future consideration | 38 | none — passthrough |

Counts are from `data/resolved/classified_elements.jsonl` (1224 elements over 413 trades,
3 still `unresolved`).

**Pick tiers:** lottery (top ~10) / mid-1st (11-20) / late-1st (21-32) / 2nd round / 3rd round+

---

## Input / Output Format

Each training example is a (prompt, JSON) pair. Player names are **excluded** from prompts — the model learns from characteristics, not identities.

**Input prompt example (NHL Skater):**
```
Position: C, depth (3rd-4th line)
Age: 31, rental (UFA end of season)
Cap hit: $1.5M (50% retained)
Stats: 43 GP, 8G 4A, 0.28 pts/GP, 13:40 TOI, 56.7% FO, 78 hits
Context: defensive center known for elite PK, energy and forecheck,
         locker room leadership, reliable depth forward.
Traded with: []
Market context: trade deadline, selling team

What package does this player return in a trade?
```

**Output JSON example:**
```json
{
  "traded_with": [],
  "return": {
    "players": [],
    "picks": [
      {
        "tier": "3rd_round",
        "year": 2026,
        "conditional": true,
        "condition": "becomes 2nd if acquiring team makes playoffs"
      }
    ]
  }
}
```

The `traded_with` field contains full profiles of co-traded assets, using the same schema as the main player. This allows the model to learn that return packages are conditional on the full composition of a trade.

---

## Core Constraint — Snapshot at Trade Date

Every enrichment must reflect what was knowable **the day before the trade**. Otherwise the
model leaks the future (a prospect who became a star three years later). Concretely:

- Stats are recomputed from the NHL game log, cut off at the trade date — not season totals
- Web searches pass `end_date=trade_date` so no post-trade article can surface
- Pick tiers use the original team's standing at the trade date, not the actual draft result

## Pipeline

### Step 1 — Scrape TSN Trade Tracker API ✅
- Script: `pipelines/scrape_tsn.py`
- Endpoint: `https://next-gen.sports.bellmedia.ca/v2/trades/hockey/nhl?brand=tsn&lang=en`
- Output: `data/raw/tsn/all.json`
- Each trade contains: teams, players (with NHL player ID when available), picks, TSN article URL for major trades
- Schema documented in `docs/tsn_schema.md`

### Step 2 — Normalization ✅
- Script: `pipelines/normalize_trades.py`
- Flattens the raw TSN payload into one canonical record per trade
- Fixes picks and salary retentions mis-encoded by TSN as players
- Output: `data/normalized/trades.jsonl` (413 trades)

### Step 3 — Player ID Resolution ✅
- Script: `pipelines/resolve_ids.py`
- TSN provides `nhl_id` for most but not all players; missing ones are resolved by
  slugifying the name and reading CapWages' `__NEXT_DATA__` payload
- Manual escape hatch: `data/manual/name_overrides.json`
- Output: `data/resolved/player_id_map.json`

### Step 4 — Element Classification ✅
- Script: `pipelines/classify_elements.py`
- Assigns each trade element its type, using the NHL API at the trade date:
  - `/player/{id}/landing` → position (skater vs goalie), age, height/weight
  - `/player/{id}/game-log/{season}/2` → NHL games played before the trade
- NHL player vs. prospect is decided by games played prior to the trade
- Output: `data/resolved/classified_elements.jsonl` (1224 elements)

### Step 5 — Article Prefetch ✅ (extraction ❌)
- Scripts: `pipelines/prefetch_trade_articles.py`, `pipelines/prefetch_prospect_articles.py`
- Web search runs through **Tavily** (`pipelines/sources/web_search.py`), not Google News RSS:
  - `include_domains` restricts results to a whitelist of hockey outlets
  - `end_date` bounds results to the trade date
  - every search and article is cached on disk (`data/raw/search/`, `data/raw/articles/`)
    so reruns cost no API credits
- Trades with a working TSN article URL use it directly; the rest fall back to search
- Current cache: 214 searches, 282 articles
- **Still missing:** the LLM distillation step that turns raw article text into
  `qualitative_summary` / `qualitative_signals`

### Step 6 — Element Enrichment ❌
Each classified element is enriched according to its type via dedicated source modules:

**Source modules** (`pipelines/sources/`):
- `web_search.py` — Tavily search + article text extraction, with disk cache ✅
- `nhl_api.py` — basic stats, TOI, position (logic currently inlined in `classify_elements.py`, to extract)
- `moneypuck.py` — advanced stats (xG, GSAx, CF%, etc.)
- `puckpedia.py` — contract details, cap hit, UFA/RFA status, NMC/NTC clauses
- `eliteprospects.py` — prospect stats, rankings, scouting reports

**Enrichment scripts** (`pipelines/`):
- `enrich_skater_nhl.py`
- `enrich_skater_prospect.py`
- `enrich_goalie_nhl.py`
- `enrich_goalie_prospect.py`
- `enrich_pick.py`

Target field lists per type live in `docs/structure_*.md`.

### Step 7 — Build Training Dataset ❌
- Construct (input prompt, output JSON) pairs for each traded player
- Each player in a multi-player trade gets its own prompt
- Co-traded assets appear as full profiles in the `traded_with` field

### Step 8 — Data Augmentation ❌
- Slight stat variations (±10-15%) with coherent qualitative text adjustments
- Generate variations via LLM given original profile + "generate 5 realistic variations"
- Output JSON stays identical or varies slightly to reflect modified stats

### Step 9 — Fine-tuning ❌
- **Platform: Azure** (replaces the earlier RunPod plan) — funded by existing Azure
  credits that expire around **October 2026**, which sets the project deadline
- Base model: 7-8B class, LoRA. Mistral 7B / Llama 3 8B were the original picks;
  re-evaluate against what's current before committing
- Output: structured JSON (picks, players, tiers)

**Two Azure routes:**
- Azure ML on a self-managed GPU VM (`Standard_NC*_A100_v4` family) — full control
- Azure AI Foundry managed fine-tuning — upload the JSONL, less control, smaller model catalog

**Check GPU quota first.** A100/H100 VMs are not available by default; quota increases
are requested per region, take hours to days, and are sometimes denied on
credit-program subscriptions. Verify this early — a refusal changes the plan, and it's
better to find out months before the credits expire.

**Budget reality:** a LoRA run over ~1000 short examples is 1-3 hours on a single A100,
so the real cost is tens of dollars even across many iterations. The credits are not
the constraint; GPU quota and dataset size are.

---

## Data Sources

| Source | Used for | Access | Status |
|---|---|---|---|
| TSN Trade Tracker | trade list, teams, dates, elements | public JSON endpoint | ✅ in use |
| NHL API (`api-web.nhle.com`) | position, age, game logs, standings | public, no key | ✅ in use |
| Tavily | web search + article text | API key in `.env` (paid credits) | ✅ in use |
| CapWages | fallback `nhl_id` resolution | scrape `__NEXT_DATA__` | ✅ in use |
| PuckPedia | contracts: cap hit, term, UFA/RFA, NMC/NTC, retention | `puckpedia.com/player/{slug}` | ❌ decided, not built |
| MoneyPuck | advanced stats (xG, GSAx, CF%) | bulk CSV per season | ❌ not started |
| EliteProspects | prospect stats, rankings, scouting | TBD | ❌ not started |

**PuckPedia caveat:** the site sits behind a Cloudflare challenge — a plain `urllib`/`curl`
request returns 403, unlike CapWages. Scraping it will need a headless browser or an
official API, so it can't reuse the same fetch helpers as the rest of the pipeline.

**Cost note:** Tavily is the only paid source. Both prefetch scripts are idempotent and
read from the disk cache first, so reruns are free unless the query string changes.

---

## Repository Structure

```
pipelines/
  scrape_tsn.py             # step 1
  normalize_trades.py       # step 2
  resolve_ids.py            # step 3
  classify_elements.py      # step 4
  prefetch_*_articles.py    # step 5
  sources/                  # one module per data source
  enrich_*.py               # one enrichment script per trade element type (todo)
data/                       # gitignored except manual/
  manual/                   # name_overrides.json — hand-fixed ID resolutions
  raw/tsn/                  # raw TSN API JSON
  raw/search/               # cached Tavily search results
  raw/articles/             # cached article text
  normalized/               # trades.jsonl
  resolved/                 # player_id_map.json, classified_elements.jsonl
  enriched/                 # enriched trade elements (todo)
  training/                 # final (prompt, output) pairs (todo)
docs/
  tsn_schema.md             # TSN API schema
  tsn_normalization_draft.md
  structure_*.md            # target field lists per element type
logs/
```

---

## Setup

```bash
pip install -r requirements.txt
echo "TAVILY_API_KEY=..." > .env
```

Run the pipeline in order — each step reads the previous step's output:

```bash
python pipelines/scrape_tsn.py
python pipelines/normalize_trades.py
python pipelines/resolve_ids.py
python pipelines/classify_elements.py
python pipelines/prefetch_trade_articles.py
python pipelines/prefetch_prospect_articles.py
```

---

## Next Up

1. **Contract enrichment (PuckPedia)** — blocks all 525 NHL players; cap hit and
   UFA/RFA status are likely the strongest predictors of trade return
2. **Pick tier estimation** — 456 elements (37% of the dataset) with no code yet;
   needs historical NHL standings at the trade date
3. **LLM distillation of cached articles** — 282 articles sitting unused as raw text
4. **Formal per-type schemas** — before writing the `enrich_*.py` scripts

Runs in parallel, not on the critical path but time-sensitive:

- **Request Azure GPU quota now** — the answer can take days and can be no; the credits
  expire in October 2026 and the data work doesn't depend on it

---

## Key Design Decisions

- **No player names in prompts** — model learns value from characteristics, not identity. Enables cleaner data augmentation.
- **Heterogeneous inputs handled via LLM** — stats + free text naturally combined without forcing a unified feature matrix across skaters, prospects, picks and goalies.
- **traded_with field** — allows the model to learn that return packages depend on the full composition of the trade, not just the main piece.
- **Text enrichment via LLM distillation** — qualitative characteristics (leadership, defensive identity, playoff performer reputation) extracted from journalism, not invented.
- **Pick tier over raw round** — encodes the true expected value of a pick at trade time, accounting for team context and protection conditions.