# TSN Trade Tracker schema

## Source snapshot
- **Endpoint**: `https://next-gen.sports.bellmedia.ca/v2/trades/hockey/nhl?brand=tsn&lang=en`
- **Response shape**: a flat array of 413 trade objects (captured 2026-03-13). The range of `tradeDate` values runs from `2022-06-16T21:40:00.000Z` through `2026-03-13T20:04:00.000Z`.
- **Coverage**: the dataset spans the 2022–2026 seasons (38 trades in 2022, 108 in 2023, 107 in 2024, 103 in 2025, 57 in 2026) with 144 of the entries flagged as `isCanadianTrade` and 84 marked as `brandsExtraInfo.TSN.isMajorTrade`.
- **Parameter notes**: query modifiers such as `season`, `limit`, `page`, `offset` are ignored—every request returns the same 413-item payload. Repeating the call several times (with and without modifiers) always yielded HTTP 200, so no rate limit was observable in this manual pass.

## Trade object (per array entry)
Each element represents one TSN-tracked transaction and exposes the following guaranteed fields:

- `id`: string in the form `HOCKEY:NHL:<year>:<tradeId>`.
- `tradeId`: integer.
- `tradeDate`: ISO 8601 UTC timestamp (`2026-03-06T21:02:00.000Z`).
- `isCanadianTrade`: boolean flag.
- `competitorOne` and `competitorTwo`: team descriptors (see below).
- `brandsExtraInfo`: brand map, typically only `TSN`.
- `tradeAcquisitions`: object with `competitorOne` and `competitorTwo` arrays of returned assets.
- `editedInformation`: optional object (first seen in late 2024) described under _Edited information_.

```json
{
  "id": "HOCKEY:NHL:2026:5208",
  "tradeId": 5208,
  "tradeDate": "2026-03-06T21:02:00.000Z",
  "isCanadianTrade": true,
  "competitorOne": {
    "competitorId": 20,
    "shortName": "CGY",
    "name": "Calgary Flames",
    "seoIdentifier": "calgary-flames"
  },
  "competitorTwo": {
    "competitorId": 21,
    "shortName": "COL",
    "name": "Colorado Avalanche",
    "seoIdentifier": "colorado-avalanche"
  },
  "brandsExtraInfo": {
    "TSN": {
      "isMajorTrade": true,
      "informations": "Calgary retains 20 per cent of Kadri's salary.",
      "url": "https://www.tsn.ca/nhl/article/avalanche-re-acquire-kadri-from-flames/"
    }
  },
  "tradeAcquisitions": {
    "competitorOne": [
      {
        "playerId": 8478109,
        "playerName": "Victor Olofsson",
        "position": "Left Wing",
        "positionFr": "Ailier gauche",
        "positionShort": "LW",
        "seoIdentifier": "victor-olofsson"
      },
      {
        "playerName": "Max Curran"
      },
      {
        "draftPickRound": 1,
        "draftPickYear": 2028,
        "isConditional": true,
        "title": "Conditional 2028 1st Round Pick"
      },
      {
        "draftPickRound": 2,
        "draftPickYear": 2027,
        "isConditional": true,
        "title": "Conditional 2027 2nd Round Pick"
      }
    ],
    "competitorTwo": [
      {
        "playerId": 8475172,
        "playerName": "Nazem Kadri",
        "position": "Centre",
        "positionFr": "Centre",
        "positionShort": "C",
        "seoIdentifier": "nazem-kadri"
      },
      {
        "draftPickRound": 4,
        "draftPickYear": 2027,
        "isConditional": false,
        "title": " 2027 4th Round Pick"
      }
    ]
  },
  "editedInformation": {
    "editedBy": {
      "name": "Adam Kirshenblatt",
      "email": "adam.kirshenblatt@bellmedia.ca",
      "role": "EDITOR"
    },
    "editedDate": "Fri, 06 Mar 2026 21:49:56 GMT"
  }
}
```

## Team object
Each `competitor` object contains:

- `competitorId`: numeric team identifier.
- `shortName`: three-letter code.
- `name`: full organization name.
- `seoIdentifier`: hyphenated slug used in TSN URLs.

## Trade acquisition entries
Every acquisition array mixes the four canonical shapes below. There are ~415 entries with a `playerId`, ~740 entries that define `playerName`, ~454 pick entries, and 31 explicit `isFutureConsideration` markers in the current snapshot.

- **Player with NHL ID**: includes `playerId`, `playerName`, the English `position`, the French `positionFr`, `positionShort`, and `seoIdentifier`.

```json
{
  "playerId": 8478109,
  "playerName": "Victor Olofsson",
  "position": "Left Wing",
  "positionFr": "Ailier gauche",
  "positionShort": "LW",
  "seoIdentifier": "victor-olofsson"
}
```

- **Player without NHL ID**: only `playerName` is populated (example: prospects or minor-league swaps).

```json
{
  "playerName": "Josh Bloom"
}
```

- **Draft pick entry**: always carries `draftPickRound` and `draftPickYear`. Optional fields include `title`, `isConditional`, and `isFutureConsideration`. Some picks keep `playerId`, `playerName`, or `positionShort` empty for schema uniformity.

```json
{
  "draftPickRound": 1,
  "draftPickYear": 2028,
  "isConditional": true,
  "title": "Conditional 2028 1st Round Pick"
}
```

- **Future consideration**: when the return is not yet defined, only the boolean `isFutureConsideration` is present (usually `true`).

```json
{
  "isFutureConsideration": true
}
```

## Brand metadata (`brandsExtraInfo`)
- Typically `{ "TSN": { ... } }`. Within `TSN` we see:
  - `isMajorTrade` (`boolean`; 84 trades are marked `true`).
  - `informations` (`string` or `null`; e.g., "Calgary retains 20 per cent of Kadri's salary.").
  - `url` (TSN article link or `null`).
- Two trades (IDs 5019 and 5018, both from December 2024) have an empty `brandsExtraInfo` object with no `TSN` key.

## Edited information
- Optional object introduced in late 2024 and present on 165 trades since then.
- Contains `editedBy` (`name`, `email`, `role`) and `editedDate` presented in RFC 2822 (`Fri, 06 Mar 2026 21:49:56 GMT`).

## Pagination and rate limiting
- The payload is *not* paginated: parameters such as `season`, `limit`, `page`, and `offset` produce the same 413 trades, so clients must fetch the whole array in one request.
- Manual probing (multiple sequential requests both with and without modifiers) always returned HTTP 200; no `429` or throttling headers were observed, so there is no documented limit during light scraping.

## Seasonal variations
- **2022 (38 trades)**: Earliest entries show zero `editedInformation` and no `position` metadata inside acquisition entries. `TSN` metadata is always present and usually includes an article `url`.
- **2023 (108 trades)**: Still no `editedInformation` or `position` fields, but pick coverage increases.
- **2024 (107 trades)**: `editedInformation` first appears (14 trades). `position`/`seoIdentifier` values start to appear (21 player entries). Two trades (IDs 5019 & 5018) have no `TSN` block.
- **2025 (103 trades)**: The majority of trades (96) carry `editedInformation`. Player entries consistently include position data and `seoIdentifier` slugs.
- **2026 (57 trades)**: `editedInformation` is almost universal (55 trades). `isMajorTrade` continues to flag a subset (11 trades so far), and `brandsExtraInfo.TSN.url` still points to TSN article pages when available.

## Summary counts (current snapshot)
- Total trades returned: 413.
- Canadian-trade flag: 144 `isCanadianTrade = true`.
- Players carrying `playerId`: 415 acquisition records.
- Draft picks: 454 acquisition records.
- Future considerations: 31 acquisition records with `isFutureConsideration = true`.
