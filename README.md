# NHL Trade Market Predictor

## Objective

Given a player's profile (stats, contract, qualitative context), predict the trade package that player would fetch on the NHL trade market — expressed as a structured JSON output containing picks, prospects, and roster players.

The model is a small fine-tuned LLM (Mistral 7B or Llama 3 8B) trained on (input prompt, output JSON) pairs built from enriched historical NHL trades.

---

## Trade Element Types

Each side of a trade is decomposed into typed elements, each with its own enrichment schema:

| Type | Key Sources |
|---|---|
| NHL Skater | NHL API, MoneyPuck, PuckPedia, TSN article or RSS |
| Skater Prospect | EliteProspects, RSS/scouting |
| NHL Goalie | NHL API, MoneyPuck (GSAx), RSS |
| Goalie Prospect | EliteProspects, RSS |
| Pick | Tier estimation, original team, year |

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

## Pipeline

### Step 1 — Scrape TSN Trade Tracker API
- Endpoint: `https://next-gen.sports.bellmedia.ca/v2/trades/hockey/nhl?brand=tsn&lang=en`
- Store raw JSON, one file per season
- Each trade contains: teams, players (with NHL player ID when available), picks, TSN article URL for major trades

### Step 2 — Player ID Resolution
- Match player names to NHL player IDs via fuzzy matching
- Fall back to local LLM for ambiguous cases

### Step 3 — Element Enrichment
Each trade element is enriched according to its type via dedicated source modules:

**Source modules** (`pipelines/sources/`):
- `nhl_api.py` — basic stats, TOI, position, contract status
- `moneypuck.py` — advanced stats (xG, GSAx, CF%, etc.)
- `puckpedia.py` — contract details, cap hit, UFA/RFA status, NMC/NTC clauses
- `eliteprospects.py` — prospect stats, rankings, scouting reports
- `rss.py` — Google News RSS scraper, filtered by player name and pre-trade date

**Enrichment scripts** (`pipelines/`):
- `enrich_skater_nhl.py`
- `enrich_skater_prospect.py`
- `enrich_goalie_nhl.py`
- `enrich_goalie_prospect.py`
- `enrich_pick.py`

**Text enrichment logic:**
- Major trades (TSN article URL available): scrape TSN article directly
- Minor trades (no URL): query Google News RSS filtered before trade date, extract qualitative characteristics via LLM distillation

### Step 4 — Build Training Dataset
- Construct (input prompt, output JSON) pairs for each traded player
- Each player in a multi-player trade gets its own prompt
- Co-traded assets appear as full profiles in the `traded_with` field

### Step 5 — Data Augmentation
- Slight stat variations (±10-15%) with coherent qualitative text adjustments
- Generate variations via LLM given original profile + "generate 5 realistic variations"
- Output JSON stays identical or varies slightly to reflect modified stats

### Step 6 — Fine-tuning
- Base model: Mistral 7B or Llama 3 8B
- Platform: RunPod
- Output: structured JSON (picks, players, tiers)

---

## Repository Structure

```
pipelines/
  sources/         # one module per data source
  enrich_*.py      # one enrichment script per trade element type
data/
  raw/             # raw TSN API JSON by season
  enriched/        # enriched trade elements
  training/        # final (prompt, output) pairs
training/
  finetune.py
  augment.py
prompts/
  templates/       # prompt templates by element type
```

---

## Key Design Decisions

- **No player names in prompts** — model learns value from characteristics, not identity. Enables cleaner data augmentation.
- **Heterogeneous inputs handled via LLM** — stats + free text naturally combined without forcing a unified feature matrix across skaters, prospects, picks and goalies.
- **traded_with field** — allows the model to learn that return packages depend on the full composition of the trade, not just the main piece.
- **Text enrichment via LLM distillation** — qualitative characteristics (leadership, defensive identity, playoff performer reputation) extracted from journalism, not invented.
- **Pick tier over raw round** — encodes the true expected value of a pick at trade time, accounting for team context and protection conditions.