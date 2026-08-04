# NHL Trade Market Predictor

## Objective

Given a player's profile (stats, contract, qualitative context), predict the trade package that player would fetch on the NHL trade market — expressed as a structured JSON output containing picks, prospects, and roster players.

The model is a small fine-tuned LLM trained on (input prompt, output JSON) pairs built from enriched historical NHL trades.

---

## Trade Element Types

Each side of a trade is decomposed into typed elements:

| Type | Count |
|---|---|
| NHL Skater | 480 |
| Skater Prospect | 192 |
| NHL Goalie | 45 |
| Goalie Prospect | 10 |
| Pick | 456 |
| Future consideration | 38 |

Counts are from `data/resolved/classified_elements.jsonl` (1224 elements over 413
trades from 2022-06-16 to 2026-03-13; 3 still `unresolved`).

**Pick tiers:** lottery (top ~10) / mid-1st (11-20) / late-1st (21-32) / 2nd round / 3rd round+

**Caveat on the type split.** `nhl_skater` vs `skater_prospect` is decided by NHL games
played before the trade. That conflates two very different cases: a 19-year-old drafted
13th overall, and a 30-year-old career AHL forward. Both have zero NHL games; almost
nothing else about them is comparable. The research step below is expected to tell them
apart on its own — but any code that keys off `type_classified` should not assume
"prospect" means "young player with upside".

---

## Architecture

The pipeline has two halves, and the split matters:

**Deterministic.** Everything that must be exact and reproducible — the trade itself,
the target label, stats cut at the trade date, pick tiers. Computed from APIs, never
from a model.

**Researched.** The qualitative profile of each player as they were perceived at the
trade date. Produced by an LLM agent that searches the web per (player, trade date),
because no single structured source covers the range of career stages in the dataset.

This replaced an earlier design built on Tavily search plus per-source scrapers
(PuckPedia, MoneyPuck, EliteProspects, DobberProspects, TheHockeyWriters). That work is
preserved on the `archive/tavily-pipeline-2026-07-28` branch. It was abandoned because
source coverage turned out to depend on the player's career stage at the trade date, and
no combination of scrapers covered all of it — while a research agent handles the whole
range with one prompt.

---

## Core Constraint — Snapshot at Trade Date

Every enrichment must reflect what was knowable **the day before the trade**. Otherwise
the model leaks the future (a prospect who became a star three years later). Concretely:

- Stats are recomputed from the NHL game log, cut off at the trade date — not season totals
- Pick tiers use the original team's standing at the trade date, not the actual draft result
- The research agent is instructed to write as of the trade date, and never to use the
  player's later career — neither explicitly, nor to decide what to emphasize

The last one is the hard case. A model researching a 2018 trade knows how the player
turned out, and hindsight leaks through emphasis rather than through any single sentence
a filter could catch.

**Leakage is about the return, not the publication date.** An article written after the
trade that describes the player is legitimate input. What must never appear is the
package the player fetched, who went the other way, or any evaluation of the trade —
that is the prediction target.

---

## Input / Output Format

Each training example is a (prompt, JSON) pair. Player names are **excluded** from
prompts — the model learns from characteristics, not identities.

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

The `traded_with` field contains full profiles of co-traded assets, using the same schema
as the main player. This allows the model to learn that return packages are conditional
on the full composition of a trade.

---

## Pipeline

### Step 1 — Scrape TSN Trade Tracker ✅
- Script: `pipelines/scrape_tsn.py`
- Endpoint: `https://next-gen.sports.bellmedia.ca/v2/trades/hockey/nhl?brand=tsn&lang=en`
- Output: `data/raw/tsn/all.json`
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
- **Known gap:** 11 skater prospects still have no `nhl_id`

### Step 4 — Element Classification ✅
- Script: `pipelines/classify_elements.py`
- Assigns each trade element its type, using the NHL API at the trade date:
  - `/player/{id}/landing` → position (skater vs goalie), age, height/weight
  - `/player/{id}/game-log/{season}/2` → NHL games played before the trade
- Output: `data/resolved/classified_elements.jsonl` (1224 elements)

### Step 5 — Player Research ❌
- Script: `pipelines/research_player.py` (to write)
- One request per (player, trade date), to the **Azure OpenAI Responses API with the
  hosted `web_search` tool** — the model runs the search loop server-side:

  ```
  POST {AZURE_OPENAI_ENDPOINT}/openai/v1/responses?api-version=preview
  body  {"model": "gpt-5.5", "input": <prompt>, "tools": [{"type": "web_search"}]}
  ```

  Output blocks are `reasoning`, `web_search_call`, `message`; source URLs come back
  as `annotations` on the `message` block. No client-side tool loop, no search API,
  no page fetching. Billed as Azure OpenAI usage, so covered by the credits.
- Produces a prose profile of the player as perceived at the trade date, with sources
- Cached on disk by `(trade_id, element_index)` so reruns cost nothing
- Output: `data/raw/briefs/`

Grounding with Bing — the Foundry Agent Service route — is **not usable here**: the
subscription's `quotaId` is `Sponsored_2016-01-01`, and Bing resources are barred on
sponsored subscriptions (`SkuNotEligible` on SKU G1). The Responses API's `web_search`
tool reaches search through Azure OpenAI instead, which works. See `plan.md`.

The agent is given the trade context — it needs it to find the right articles — and is
instructed not to reproduce it. The leaky material stays quarantined in `raw/`.

### Step 6 — Profile Extraction ❌
- Script: `pipelines/extract_profile.py` (to write)
- One plain API call per element, no agent loop: brief in, training profile out
- Applies the leakage rules and strips the player's name
- Output: `data/enriched/profiles.jsonl`

Splitting research from extraction means the leakage rules can be changed and rerun
against cached briefs without repeating a single web search. A prior version of this
step, written against Tavily article text, is on the archive branch — its JSON schema,
system prompt and `validate()` function are worth lifting.

### Step 7 — Deterministic Enrichment ❌
Three things that must never come from the agent:

- **Stats cut at the trade date** — from the NHL game log. Also serves as a
  hallucination check: compare against any stats the agent asserts, and flag the gaps
- **Pick tiers** — 456 elements, from NHL standings at the trade date. No code yet
- **The output JSON** — the target label, built from `data/normalized/trades.jsonl`

### Step 8 — Build Training Dataset ✅
- Script: `pipelines/build_training_dataset.py`
- One example per player element among the 727 with a completed E6 profile (399
  TSN-sourced trades, 2022-06 to present — the nhltradetracker extension to 2005 has
  no E5 research pass yet, see caveat below)
- `traded_with` = other elements sent in the same package (same `receives_key`,
  other indices): full profile for players, compact tier for picks, raw text for
  future considerations
- `output` (the target) = the other side's return: picks with tier (from
  `enrich_pick.py`), players reduced to `{type, position}` — no formula for player
  value exists (unlike picks), so nothing is invented — future considerations as text
- No cap hit, structured contract status, or `age_at_trade`: no pipeline step
  computes them deterministically. The research agent's prose mentions age/contract
  informally; nothing is fabricated to fill the gap
- `market_window` is a coarse deterministic label from the trade date (offseason /
  near trade deadline / in-season), not a buyer/seller signal — that would need
  standings-based logic not yet built
- Output: `data/training/dataset.jsonl` (727 examples)

**Fixed during assembly**: ~126/727 E6 profiles (17%) had come back in French despite
the extraction system prompt requiring English — an unnoticed gap in `extract_profile.py`'s
prompt discipline. Manually translated in place (`data/raw/extractions/gpt-5.4-mini/v2/`
cache, then `profiles.jsonl`/`profiles_deidentified.jsonl` rebuilt from cache). Also found
one third-party name leak the existing `validate()` doesn't catch — it only checks the
subject's own name and the return-side names, not other people mentioned in the prose
(trade 5031 named a teammate). Redacted in place. **Follow-up needed**: `validate()` in
`extract_profile.py` should reject any capitalized proper name beyond the documented
false-positive set (countries, leagues, colleges, awards), not just the subject/return
names — the current pass was a manual, one-time catch, not a rebuilt guardrail.

### Step 9 — Data Augmentation ❌
- Slight stat variations (±10-15%) with coherent qualitative text adjustments
- Output JSON stays identical or varies slightly to reflect modified stats

### Step 10 — Fine-tuning ❌
- **Platform: Azure** — funded by existing Azure credits that expire around
  **October 2026**, which sets the project deadline
- Base model: 7-8B class, LoRA. Re-evaluate against what's current before committing
- Output: structured JSON (picks, players, tiers)

**Two Azure routes:** Azure ML on a self-managed GPU VM (`Standard_NC*_A100_v4`), or
Azure AI Foundry managed fine-tuning.

**Check GPU quota first.** A100/H100 VMs are not available by default; quota increases
are requested per region, take hours to days, and are sometimes denied on
credit-program subscriptions. A refusal changes the plan, and it is better to find out
months before the credits expire.

**Budget reality:** a LoRA run over ~1000 short examples is 1-3 hours on a single A100.
GPU quota and dataset size are the constraints, not credit.

---

## Data Sources

| Source | Used for | Access | Status |
|---|---|---|---|
| TSN Trade Tracker | trade list, teams, dates, elements | public JSON endpoint | ✅ in use |
| NHL API (`api-web.nhle.com`) | position, age, game logs, standings | public, no key | ✅ in use |
| CapWages | fallback `nhl_id` resolution | scrape `__NEXT_DATA__` | ✅ in use |
| LLM agent + web search | qualitative player profiles | Claude API | ❌ not built |

**On funding the research step.** Azure credits pay for fine-tuning. They only reach the
research agent through Claude on Microsoft Foundry, billed via Azure Marketplace — and
many Azure credit programs exclude Marketplace purchases. Verify the grant's terms
before assuming the agent is covered.

---

## Repository Structure

```
pipelines/
  scrape_tsn.py             # step 1
  normalize_trades.py       # step 2
  resolve_ids.py            # step 3
  classify_elements.py      # step 4
  research_player.py        # step 5
  extract_profile.py        # step 6
  enrich_pick.py             # step 7 (picks)
  enrich_stats.py            # step 7 (stats)
  build_training_dataset.py # step 8
data/                       # gitignored except manual/
  manual/                   # name_overrides.json — hand-fixed ID resolutions
  raw/tsn/                  # raw TSN API JSON
  raw/briefs/               # cached agent research briefs
  raw/extractions/          # cached E6 profile extractions
  raw/picks/, raw/stats/    # cached E7 enrichment
  normalized/               # trades.jsonl
  resolved/                 # player_id_map.json, classified_elements.jsonl
  enriched/                 # profiles.jsonl, profiles_deidentified.jsonl, picks.jsonl, stats.jsonl
  training/                 # dataset.jsonl — final (prompt, output) pairs
docs/
  tsn_schema.md             # TSN API schema
  tsn_normalization_draft.md
logs/
```

`data/raw/search/` and `data/raw/articles/` hold 214 cached Tavily searches and 282
article texts from the previous design. They are untracked and cost nothing to keep;
delete them once the agent approach is validated.

---

## Setup

```bash
pip install -r requirements.txt
```

Run the pipeline in order — each step reads the previous step's output:

```bash
python pipelines/scrape_tsn.py
python pipelines/normalize_trades.py
python pipelines/resolve_ids.py
python pipelines/classify_elements.py
```

---

## Next Up

1. **Write the research agent** and pilot it on 5-10 elements spanning career stages.
   This gives the real per-element cost, and therefore the total — the number that
   decides whether the approach is viable at 727 players
2. **Pick tier estimation** — 456 elements (37% of the dataset) with no code yet
3. **Request Azure GPU quota** — the answer can take days and can be no; it does not
   depend on the data work, and the credits expire October 2026

---

## Key Design Decisions

- **No player names in prompts** — model learns value from characteristics, not identity.
  Enables cleaner data augmentation.
- **Research agent over per-source scrapers** — source coverage depends on career stage
  at the trade date, and no scraper set covered the whole range. Validated on the hard
  cases: an anonymous 30-year-old AHL veteran in a one-for-one minor trade, and a
  throw-in prospect in a blockbuster where all coverage was about someone else.
- **Research separated from extraction** — leaky material stays in `raw/`; the rules can
  be rerun without repeating a search.
- **Deterministic core stays deterministic** — target label, stats, and pick tiers are
  computed, never generated. Agent-asserted stats are checked against them.
- **traded_with field** — return packages depend on the full composition of the trade,
  not just the main piece.
- **Pick tier over raw round** — encodes expected value at trade time, accounting for
  team context and protection conditions.
