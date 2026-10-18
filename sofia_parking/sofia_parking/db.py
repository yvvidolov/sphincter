import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    kind TEXT,                 -- ГАРАЖ / ПАРКОМЯСТО as shown on the site
    price_eur REAL,
    price_text TEXT,
    price_trend TEXT,          -- UP / DOWN marker from the list page
    vat_note TEXT,
    area_m2 REAL,
    floor TEXT,
    street_hint TEXT,          -- street field chosen by the seller
    snippet TEXT,
    seller TEXT,
    card_hash TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    detail_fetched_at TEXT,
    description TEXT,
    features TEXT,             -- JSON list
    modified_text TEXT,
    views INTEGER
);

CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL,
    price_eur REAL,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    listing_id TEXT PRIMARY KEY,
    input_hash TEXT NOT NULL,
    model TEXT,
    data TEXT NOT NULL,        -- JSON
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    listing_id TEXT PRIMARY KEY,
    extraction_hash TEXT NOT NULL,
    kind TEXT,                 -- point / street / area / none
    lat REAL,
    lon REAL,
    radius_m REAL,
    geometry TEXT,             -- JSON list of [lat, lon] polylines, for streets
    label TEXT,
    confidence REAL,
    dist_expected_m REAL,
    dist_min_m REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geocache (
    query TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def cache_get(conn, query: str):
    row = conn.execute("SELECT response FROM geocache WHERE query = ?", (query,)).fetchone()
    return json.loads(row["response"]) if row else None


def cache_put(conn, query: str, response) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO geocache (query, response, created_at) VALUES (?, ?, ?)",
        (query, json.dumps(response, ensure_ascii=False), now()),
    )
    conn.commit()
