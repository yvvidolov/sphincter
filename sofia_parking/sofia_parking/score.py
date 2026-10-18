"""Rank active listings by distance to the target, price and location confidence."""

import json
import re
from datetime import datetime, timedelta, timezone

# Every listing is in the "Център" district, so an unknown location is still reasonably close.
UNKNOWN_DISTANCE_SCORE = 0.25

MONTHS = {m: i for i, m in enumerate(
    ("януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври",
     "ноември", "декември"), 1)}
SITE_DATE_RE = re.compile(r"(Публикувана|Коригирана) в (\d+):(\d+) на (\d+) (\S+), (\d{4})")


def site_date(text: str | None) -> tuple[datetime | None, str | None]:
    """'Коригирана в 10:55 на 9 октомври, 2026 год.' -> (datetime, "edited")."""
    m = SITE_DATE_RE.search(text or "")
    if not m or m[5] not in MONTHS:
        return None, None
    dt = datetime(int(m[6]), MONTHS[m[5]], int(m[4]), int(m[2]), int(m[3]))
    return dt, "posted" if m[1] == "Публикувана" else "edited"


def ranked(conn, cfg: dict) -> list[dict]:
    sc = cfg["score"]
    rows = conn.execute(
        """SELECT l.*, e.data AS extraction, loc.kind AS loc_kind, loc.lat, loc.lon, loc.radius_m,
                  loc.geometry, loc.label AS loc_label, loc.confidence AS loc_conf,
                  loc.dist_expected_m, loc.dist_min_m
           FROM listings l
           LEFT JOIN extractions e ON e.listing_id = l.id
           LEFT JOIN locations loc ON loc.listing_id = l.id
           WHERE l.active = 1"""
    ).fetchall()
    prices = sorted(r["price_eur"] for r in rows if r["price_eur"])
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=3)).isoformat()
    # Listings from the very first scan aren't "new" to us, just to the database.
    first_scan = conn.execute("SELECT MIN(first_seen) FROM listings").fetchone()[0]

    out = []
    for r in rows:
        item = dict(r)
        item["extraction"] = json.loads(r["extraction"]) if r["extraction"] else {}
        item["geometry"] = json.loads(r["geometry"]) if r["geometry"] else None

        if r["dist_expected_m"] is not None:
            d = (r["dist_expected_m"] + r["dist_min_m"]) / 2
            dist_score = max(0.0, 1 - d / sc["max_distance_m"])
        else:
            d, dist_score = None, UNKNOWN_DISTANCE_SCORE
        if r["price_eur"] and len(prices) > 1:
            # Share of listings that are more expensive.
            price_score = sum(p > r["price_eur"] for p in prices) / (len(prices) - 1)
        else:
            price_score = 0.0
        conf = r["loc_conf"] or 0.0

        score = 100 * (sc["weight_distance"] * dist_score + sc["weight_price"] * price_score
                       + sc["weight_confidence"] * conf)
        ex = item["extraction"]
        if ex.get("is_genuine_listing") is False:
            score *= 0.1
        if ex.get("sold_with_apartment_only"):
            score *= 0.5
        if ex.get("fits_regular_car") is False:
            score *= 0.6

        history = conn.execute(
            "SELECT price_eur FROM price_history WHERE listing_id = ? ORDER BY seen_at", (r["id"],)
        ).fetchall()
        hist = [h["price_eur"] for h in history if h["price_eur"]]
        listed_at, listed_how = site_date(r["modified_text"])
        if listed_at is None:
            listed_at = datetime.fromisoformat(r["first_seen"]).replace(tzinfo=None)
            listed_how = "first seen"
        age_days = (now.replace(tzinfo=None) - listed_at).total_seconds() / 86400
        item.update(
            score=round(score, 1),
            distance_m=d,
            is_new=r["first_seen"] >= recent and r["first_seen"] > first_scan,
            listed_at=listed_at,
            listed_how=listed_how,
            age_days=age_days,
            is_fresh=age_days <= sc["fresh_days"],
            price_drop=(hist[0] - hist[-1]) if len(hist) > 1 and hist[-1] < hist[0] else 0,
        )
        out.append(item)
    out.sort(key=lambda x: -x["score"])
    return out
