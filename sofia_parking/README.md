# Overview
Daily traverse imot.bg lists and find good buy options.
We are targeting the center of sofia, with distance from a target address (`[target]` in config.toml) as the main criterion.
We want an automatic and minimal rescan of the website, so we don't get banned.
We want to use LLM to scan the description of each listing to try and determine location and parameters.
We want some sort of sorting or recommendation, so the user can be notified of good opportunities.
We want a map view with possible location and price, location can be point, circle with probability, or just a street or general landmark.
Language is python.
imot.bg is public, no login needed.
This is the link for parking spaces in the center of sofia: https://www.imot.bg/obiavi/prodazhbi/grad-sofiya/tsentar/garazh-parkomyasto
LLM itself would be pretty bad at locations, so we need to include some sort of map, geolocation, geocoding, etc.


# Findings about imot.bg
- The "Център" garage/parking category is small: ~50 listings on 2 list pages. Garages (ГАРАЖ) and
  parking spots (ПАРКОМЯСТО) are one category; the type is only a label on each listing.
- Pages are windows-1251 encoded. `robots.txt` allows everything. No coordinates are exposed
  (the map needs a login), so location comes only from text.
- Each list card already has price, area, floor, the seller's drop-down street and a description snippet;
  price changes are marked (UP/DOWN). The drop-down street is often wrong — the description is better.

# How it works
1. `scrape` — fetch all list pages (2 requests/day) and only the detail pages that are new or whose
   list card changed (plus a refresh every 30 days). Random 4–9 s delays. Listings missing from a
   complete scan are marked inactive; price history is kept.
2. `extract` — LLM reads each listing (Bulgarian) and returns JSON: ranked location hints
   (address / intersection / street / landmark / area, with confidence and quoted evidence), parking type,
   level, VAT, title status, red flags, English summary. Re-runs only when the listing text changes.
3. `geocode` — hints → geometry via OpenStreetMap: Nominatim for addresses/landmarks/areas, Overpass for
   full street geometry. Result is a point, a circle with an uncertainty radius, or a whole street.
   Expected and minimum distance to the target are computed. All responses are cached in SQLite.
4. `score` — distance (60%), price rank (30%), location confidence (10%); weights in config.
5. `map` — `data/map.html` (Leaflet/folium): target with 500 m / 1 km rings, coloured markers,
   uncertainty circles, highlighted streets, popups, ranking panel.

# Usage
```bash
python3 -m venv .venv && .venv/bin/pip install -e .
cp config.example.toml config.toml        # set [target] address, LLM backend/model
.venv/bin/sofia-parking -v run            # scrape + extract + geocode + map + report
.venv/bin/sofia-parking report -n 10
.venv/bin/sofia-parking show <listing-id>
.venv/bin/sofia-parking run --no-llm      # no LLM: uses the drop-down street only
```

LLM backends (`[llm]` in config.toml):
- `litellm` — any model litellm supports, e.g. `anthropic/claude-sonnet-5-5` with `ANTHROPIC_API_KEY`,
  or a LiteLLM proxy via `model = "openai/<name>"` and `api_base`.
- `claude-cli` — runs `claude -p` headless, billed to the Claude subscription.

Daily run (cron):
```
17 7 * * * cd /path/to/sofia_parking && .venv/bin/sofia-parking run >> data/run.log 2>&1
```

# claude-cli lean mode (`claude_lean = true`)
By default `claude -p` sends Claude Code's own system prompt, tool definitions and settings with every
call (~19k extra input tokens). Lean mode replaces the system prompt with ours and passes
`--tools "" --strict-mcp-config --disable-slash-commands --setting-sources "" --no-session-persistence`.
It still uses the subscription login (`--bare` would not: it requires `ANTHROPIC_API_KEY`).

Measured on all 48 listings (`scripts/compare_claude_modes.py`, Sonnet, Oct 2026):

| mode | input tok/listing | output tok | time | API-equivalent cost |
|---|---|---|---|---|
| default | ~22.9k (10.3k cache writes) | ~535 | 6.9 s | ~$0.048 |
| lean | ~1.9k | ~475 | 5.3 s | ~$0.012 |

Quality: no degradation. Geocoded location matched default mode for 47–48/48 listings, the same as two
default runs match each other (47/48); distance ranking identical (Spearman 1.0). Classification fields
(parking type, title status, fits a car, …) disagree on a few listings, but equally often between two runs
of the same mode — that's model noise, not the mode.
Re-run: `python scripts/compare_claude_modes.py` (raw outputs cached in `data/compare/`).
