- [x] POIs are hard to click and not so visible
  (bigger numbered pins on top; accuracy shapes no longer intercept clicks)
- [x] When listing is clicked, show the origina listing text
  (detail pane at the top of the side panel: full description, summary, red flags, dates)
- [x] Hovering on a listing in the side menu, should highlight it on the map
- [x] Listings list information can be better, like adding address
- [x] accuracy circles around POI are visible and give information, but painting the streets for too many listings is just confusing. Maybe highlight the street only on hover, otherwise should an ellipse, to denote accuracy.
- [x] We don't know which listings are new and old, add dates. There should be an easy way to mark newest listings on list and map.
  (imot.bg posted/edited date parsed; FRESH = within `score.fresh_days` (purple ring/badge), NEW = first seen
  by us in the last 3 days, excluding the initial scan; sort by newest, "only fresh" filter)
- [x] When showing the listing locally, also add a picture.
  (photos from the list cards — no extra requests — replaced by the full gallery when a detail page is fetched;
  shown full-size in the map's detail pane, click opens the image)
- [x] After update step is finished, print how much resources that took, how many curl calls, how many llm calls and how much physical time.
  (`run` ends with a summary: imot.bg / OSM HTTP requests (+ OSM cache hits), LLM calls, tokens and cost, time per step)
- no easy way to deselect listing, just small x
- ai listing overview and original listing are small and hard to read, also the original should be the main one and the ai overview a sidenote, might be a good idea to show independently from the list of listings
- make a way to hide listings and remember the choice
- option to hide listings with larger distance than 0.5, 1, 1.5, 2km


# Suggested
- [ ] Notifications: send a message (email/Telegram/ntfy) after the daily run for new listings and price drops above a score threshold
- [ ] Price per m² column and sorting; use it as an optional scoring input
- [ ] Show price history (sparkline or list) in the listing detail pane
- [ ] Keep inactive (sold/removed) listings viewable on the map as a toggle, with how long they stayed listed — useful for market prices
- [ ] Detect duplicates: the same spot posted by several agencies (similar text/price/location) should be grouped
- [ ] Manual overrides: a small config/DB table to fix a listing's location or mark it "hidden / not interested"
- [ ] Remember the "seen" state per listing in the map (localStorage) so newly appeared listings stand out on the next visit
- [ ] Tests: unit tests for scraper parsing (saved HTML fixtures), `site_date`, scoring and `street_ellipse`
- [x] Re-install the venv: `.venv/bin/sofia-parking` had a stale shebang from the old project path (`run.sh` now repairs it)
- [ ] Mobile layout for the map: the side panel covers the whole map on a phone; make it collapsible
