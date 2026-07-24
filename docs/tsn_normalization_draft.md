# TSN Trade Normalization Draft (B1)

This document proposes a deterministic first-pass normalization strategy from TSN raw trades to a canonical JSONL format.

## Scope

- Input: `data/raw/tsn/all.json`
- Output: `data/normalized/trades.jsonl`
- Goal: normalize shape only (no enrichment, no NHL API lookups)

## Canonical trade shape

Each output line is one trade object:

```json
{
  "trade_id": 5208,
  "tsn_id": "HOCKEY:NHL:2026:5208",
  "trade_date": "2026-03-06",
  "is_canadian_trade": true,
  "is_major_trade": true,
  "tsn_url": "https://www.tsn.ca/nhl/article/avalanche-re-acquire-kadri-from-flames/",
  "informations": "Calgary retains 20 per cent of Kadri's salary.",
  "pick_origin_mentions": [
    {"owner_team": "San Jose Sharks", "round": 3}
  ],
  "team_one": {"id": 20, "short": "CGY", "name": "Calgary Flames"},
  "team_two": {"id": 21, "short": "COL", "name": "Colorado Avalanche"},
  "team_one_receives": [],
  "team_two_receives": []
}
```

## Canonical acquisition shapes

```json
{"type": "player", "nhl_id": 8478109, "name": "Victor Olofsson"}
{"type": "player", "nhl_id": null, "name": "Josh Bloom"}
{"type": "player", "nhl_id": null, "name": "Rem Pitlick", "via_team": "MTL"}
{"type": "pick", "round": 1, "year": 2028, "is_conditional": true}
{"type": "future_consideration"}
```

## Deterministic classification rules

Applied in this order per acquisition entry:

1. If `isFutureConsideration == true`: `future_consideration`.
2. Else if `playerName == "Future Considerations"` (case-insensitive): `future_consideration`.
3. Else if `draftPickRound` is present: `pick`.
4. Else if `playerName` is non-empty: `player`.
5. Else fallback: `future_consideration`.

This preserves the required behavior: player without `playerId` remains a player (typically minor/prospect), not a future consideration.

## Name cleanup and routing extraction

For player entries only:

- Strip trailing position hint: `", C"`, `", LW"`, `"(D)"`, etc.
- Extract and strip route suffix `"(via TEAM)"` to `via_team`.

Examples:

- `"Rutger McGroarty, C"` -> `"Rutger McGroarty"`
- `"Guryev, Artem (D)"` -> `"Guryev, Artem"`
- `"Rem Pitlick (via MTL)"` -> `name="Rem Pitlick"`, `via_team="MTL"`

## Current known uncertainty zones

- `playerName` sometimes carries formatting artifacts (position and routing in name string).
- Exactly two trades (5018, 5019) have no `brandsExtraInfo.TSN` block.
- Some historical entries encode future considerations as string name instead of dedicated boolean.

These are handled in B1 and can be revisited in B2/B-enrichment if needed.

## Pick ownership hints

When TSN `informations` mentions pick provenance (examples: "originally belonged to ...", "belongs to ...", or "San Jose Sharks 2025 third-round pick"), B1 stores structured hints in `pick_origin_mentions` at trade level.

- This field is optional and only present when a hint is detected.
- It does not force mapping to a specific pick asset yet.
- It preserves ownership context for later enrichment where exact pick lineage can be resolved with external sources.
