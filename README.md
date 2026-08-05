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

**Real example, from `data/training/dataset.jsonl` (trade 1548 — Kirby Dach to
Montreal, 2022-07-07):**
```
Date: 2022-07-07
Type: NHL skater
Position: C
Age: 21
Height: 6'4"
Stats: 70 GP, 9G 17A, 0.37 pts/GP, 18:02 TOI
Context: 21-year-old right-shot center, 152 NHL games; big two-way pivot with
         size, puck distribution, transition game and creative top-six upside,
         but inconsistent pace, skating burst and night-to-night impact. 26
         points in 70 games in 2021-22; RFA status, notable wrist injury
         history, development viewed as high-ceiling yet riskier than peers.
Traded with: []
Market context: offseason

What package does this player return in a trade?
```

**Output JSON:**
```json
{
  "players": [],
  "picks": [
    {"round": 1, "draft_year": 2022, "conditional": false, "estimated_pick_range": "13"},
    {"round": 2, "draft_year": 2022, "conditional": false, "estimated_pick_range": "66"}
  ],
  "future_considerations": []
}
```

`Traded with` (empty here, non-empty for multi-asset packages) carries full
profiles of co-sent assets — same schema as the subject player, still nameless —
so the model learns that a return depends on the whole package, not just the
headline piece. The output's players (empty in this example) get the same full
anonymized profile, not just a category label: a target reduced to `{type,
position}` would teach the model to classify roles instead of predicting an
actual return.

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

### Step 7 — Deterministic Enrichment ✅
Things that must never come from the agent:

- **Stats cut at the trade date** — `enrich_stats.py`, from the NHL game log
  (season + career blocks). 716/727 elements (11 with no resolved `nhl_id`)
- **Age and height at the trade date** — `enrich_bio.py`, one NHL API call per
  unique `nhl_id` (birth date + height don't depend on trade date, only age does —
  recomputed locally per trade). 718/727 elements. Weight deliberately omitted: it
  isn't a fixed adult quantity like height, and the landing endpoint only gives a
  present-day snapshot, not a value at trade_date — including it would leak
  present into past for older trades
- **Pick tiers** — `enrich_pick.py`, 2179 pick elements dataset-wide, from NHL
  standings at trade date. See the `original_owner` caveat below
- **The output JSON** — the target label, built from `data/normalized/trades.jsonl`

**`original_owner` caveat.** TSN's structured pick schema (`round`/`year`/
`isConditional`/`title`) never records which team's natural draft slot a pick
is — not a normalization gap, it's simply absent from the source. `enrich_pick.py`
originally assumed `original_owner` = whichever team handed the pick over *in that
trade*, which breaks the moment a pick has already changed hands once (found via a
user review of trade 1548: Montreal's pick sent to Chicago for Kirby Dach was
labeled `original_owner: MTL`, but it was actually the Islanders' natural 2022
1st — acquired by Montreal hours earlier the same day in trade 1544 — so the
standings-based tier estimate used Montreal's terrible 2021-22 season instead of
the Islanders' mid-table one, producing "top 4" for what was actually pick #13).

Fix applied, two layers:

1. **TSN free text.** The `informations` field sometimes states the exact overall
   pick number and/or the true original team explicitly (editorial blurb, not
   structured data) — extracted via `build_pick_number_overrides`/
   `build_original_owner_overrides` in `enrich_pick.py` when unambiguous (side's
   pick count matches the count of numbers found). Covers 19/2179 pick numbers and
   1/2179 original owners.
2. **Manual research, round-1 picks in the wm9 training scope.** Of the 27
   round-1 picks whose tier estimate actually depends on `original_owner` (i.e.
   `pick_number_source == standings_formula`, not already exact from step 1),
   3 parallel research passes (PuckPedia, CapWages, contemporary trade coverage)
   checked all 27 — **6 were wrong (22%)**: trade 3155 labeled WSH, actually BOS
   (WSH had gotten it from BOS days earlier in trade 2967); 3881 labeled CBJ,
   actually LAK; 4126 labeled MTL, actually FLA (medium confidence); 4894 labeled
   OTT, actually BOS (the pick went BOS→DET→OTT→back to BOS); 5007 labeled SJS,
   actually VGK; 5115 labeled CAR, actually DAL. Recorded in
   `data/manual/pick_owner_overrides.json`, applied via
   `original_owner_source: manual_research` (highest-confidence tier, above
   `informations_text`, above the unverified default).

`pick["pick_number_source"]` and `pick["original_owner_source"]` mark which tier
resolved each pick. Outside the round-1/wm9-scope check above, the remaining picks
still carry `original_owner_source: giving_team_assumption`, unverified — that 22%
error rate on a manually-checked sample is a reason to expect real errors there
too, not a reason to assume they're fine. A general fix (replay all 2157 trades
chronologically, track per-(team, round, year) holder) is possible whenever a team
holds only one pick of that (round, year) at re-trade time — proven on the Dach
case, since Montreal's own natural 2022 1st was never traded (used on Slafkovský)
— but not built. Follow-up: `nhl_trade_market-4v9`.

### Step 8 — Build Training Dataset ✅
- Script: `pipelines/build_training_dataset.py`
- One example per player element among the 727 with a completed E6 profile (399
  TSN-sourced trades, 2022-06 to present — the nhltradetracker extension to 2005 has
  no E5 research pass yet, see caveat below)
- Prompt fields: trade date, type, position, age, height, season stats line,
  qualitative context, `Traded with` (other elements sent in the same package —
  full profile for players, tier for picks, raw text for future considerations),
  and a coarse `Market context` (offseason / near trade deadline / in-season,
  derived from the date — not a buyer/seller signal, that needs standings logic
  not yet built)
- `output` (the target) = the other side's return, same level of detail as
  `traded_with`: full anonymized profile for players (not just a category — a
  category-only target teaches little more than a classifier), picks with tier,
  future considerations as text. Never generated by a model
- Still absent: cap hit, structured contract status — no pipeline step computes
  them deterministically; the research agent's prose mentions contract terms
  informally, nothing is fabricated to fill the structured gap
- Output: `data/training/dataset.jsonl` (727 examples, 9 missing age/height —
  same 9 with no resolved `nhl_id`)

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
