"""Turn LLM location hints into map geometry using OpenStreetMap (Nominatim + Overpass).

Every remote response is cached in the DB, so each distinct query hits the network once.
"""

import hashlib
import json
import logging
import math
import re
import time

import httpx

from .db import cache_get, cache_put, now

log = logging.getLogger(__name__)

# Uncertainty radius (m) assumed for each hint type when the geocoder gives a point.
RADIUS = {"address": 40, "intersection": 60, "landmark": 200, "area": 600}
# Lower rank = tried first.
PRIORITY = {"address": 0, "intersection": 1, "landmark": 2, "street": 3, "area": 4}
PREFIX_RE = re.compile(
    r"^\s*(ул\.?|улица|бул\.?|булевард|пл\.?|площад|ж\.?к\.?|кв\.?|пр\.?)\s+", re.I
)


# ---------------------------------------------------------------- geometry

def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _xy(p, origin):
    k = math.cos(math.radians(origin[0]))
    return ((p[1] - origin[1]) * 111320 * k, (p[0] - origin[0]) * 110540)


def point_segment_distance(p, a, b) -> float:
    px, py = _xy(p, p)
    ax, ay = _xy(a, p)
    bx, by = _xy(b, p)
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def polylines_distance(p, lines: list[list[tuple[float, float]]]) -> float:
    best = math.inf
    for line in lines:
        if len(line) == 1:
            best = min(best, haversine(p, line[0]))
        for a, b in zip(line, line[1:]):
            best = min(best, point_segment_distance(p, a, b))
    return best


def polylines_midpoint(lines) -> tuple[float, float]:
    """Point on the street closest to the centroid of all its nodes."""
    pts = [p for line in lines for p in line]
    c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    return min(pts, key=lambda p: haversine(p, c))


def polylines_length(lines) -> float:
    return sum(haversine(a, b) for line in lines for a, b in zip(line, line[1:]))


# ---------------------------------------------------------------- remote services

class Geocoder:
    def __init__(self, conn, cfg: dict):
        g = cfg["geocode"]
        self.conn = conn
        self.cfg = g
        self.bbox = g["bbox"]  # s, w, n, e
        self.http = httpx.Client(headers={"User-Agent": g["user_agent"]}, timeout=60)
        self._last = 0.0
        self.requests = 0
        self.cache_hits = 0

    def _throttle(self, seconds: float = 1.1):
        wait = seconds - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def nominatim(self, **params) -> list[dict]:
        s, w, n, e = self.bbox
        params = {
            "format": "jsonv2", "countrycodes": "bg", "limit": 5, "accept-language": "bg",
            "viewbox": f"{w},{n},{e},{s}", "bounded": 1, **params,
        }
        key = "nominatim:" + json.dumps(params, ensure_ascii=False, sort_keys=True)
        cached = cache_get(self.conn, key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self._throttle()
        self.requests += 1
        r = self.http.get(f"{self.cfg['nominatim_url']}/search", params=params)
        r.raise_for_status()
        data = r.json()
        cache_put(self.conn, key, data)
        return data

    def overpass(self, query: str) -> dict:
        key = "overpass:" + hashlib.sha1(query.encode()).hexdigest()
        cached = cache_get(self.conn, key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        urls = self.cfg["overpass_url"]
        urls = [urls] if isinstance(urls, str) else urls
        last_err = None
        for attempt in range(2):
            for url in urls:
                self._throttle(2.0)
                self.requests += 1
                try:
                    r = self.http.post(url, data={"data": query})
                    r.raise_for_status()
                    data = r.json()
                except (httpx.HTTPError, ValueError) as e:
                    last_err = e
                    log.warning("overpass %s failed: %s", url, e)
                    continue
                cache_put(self.conn, key, data)
                return data
            time.sleep(15)
        raise httpx.HTTPError(f"overpass unavailable: {last_err}")

    # ------------------------------------------------------------ hint resolvers

    @staticmethod
    def clean_street(name: str | None) -> str:
        return PREFIX_RE.sub("", name or "").strip(" .,\"'„“")

    @staticmethod
    def _tokens(name: str) -> list[str]:
        return [t.lower() for t in re.findall(r"\w+", name)]

    @classmethod
    def name_match(cls, query_toks: list[str], osm_name: str, fuzzy: bool = False) -> int | None:
        """Every query token must be a prefix of a distinct OSM name token ("Ал." ~ "Александър").
        With fuzzy, long tokens may also be up to 2 edits away (seller typos).
        Returns the number of unmatched OSM tokens (lower is better), or None for no match."""
        name_toks = cls._tokens(cls.clean_street(osm_name))
        free = list(name_toks)

        def ok(qt, nt):
            return nt.startswith(qt) or (fuzzy and len(qt) >= 6 and levenshtein(qt, nt) <= 2)

        for qt in query_toks:
            hit = next((nt for nt in free if ok(qt, nt)), None)
            if hit is None:
                return None
            free.remove(hit)
        return len(free)

    def street_ways(self, street: str) -> tuple[str, list[list[tuple[float, float]]]] | None:
        """Return (osm_name, polylines) for the best-matching street inside the bbox."""
        street = self.clean_street(street)
        toks = self._tokens(street)
        if not toks:
            return None
        anchor = max(toks, key=len)
        found = self._match_ways(toks, anchor, fuzzy=False)
        if not found and len(anchor) >= 6:
            # Typo fallback: fetch streets sharing the first 3 letters, match by edit distance.
            found = self._match_ways(toks, anchor[:3], fuzzy=True)
        if not found:
            return None
        by_name, extra = found
        # Fewest extra words ("Цар Симеон" over "Цар Симеон Велики"), then most segments.
        name = min(by_name, key=lambda k: (extra[k], -len(by_name[k])))
        return name, by_name[name]

    def _match_ways(self, toks: list[str], pattern: str, fuzzy: bool):
        s, w, n, e = self.bbox
        q = (
            f'[out:json][timeout:25];way["highway"]["name"~"{re.escape(pattern)}",i]'
            f"({s},{w},{n},{e});out geom;"
        )
        by_name: dict[str, list] = {}
        extra: dict[str, int] = {}
        for el in self.overpass(q).get("elements", []):
            name = el.get("tags", {}).get("name", "")
            score = self.name_match(toks, name, fuzzy)
            if score is not None and el.get("geometry"):
                extra[name] = score
                by_name.setdefault(name, []).append([(g["lat"], g["lon"]) for g in el["geometry"]])
        return (by_name, extra) if by_name else None

    def resolve_address(self, h: dict) -> dict | None:
        street = self.clean_street(h.get("street"))
        if not street or not h.get("number"):
            return None
        number = re.sub(r"\s+", "", str(h["number"]))
        res = self.nominatim(street=f"{number} {street}", city="София")
        if not res:
            res = self.nominatim(q=f"{street} {number}, София")
        if not res:
            # The street may be misspelled; retry with the OSM spelling.
            ways = self.street_ways(street)
            if ways and self.clean_street(ways[0]) != street:
                res = self.nominatim(street=f"{number} {self.clean_street(ways[0])}", city="София")
        for r in res:
            addr_type = r.get("addresstype") or r.get("type")
            if addr_type in ("building", "house", "place") or r.get("category") == "building":
                return {"kind": "point", "lat": float(r["lat"]), "lon": float(r["lon"]),
                        "radius_m": RADIUS["address"], "label": r.get("display_name", "")}
        return None

    def resolve_intersection(self, h: dict) -> dict | None:
        a = self.street_ways(h.get("street") or "")
        b = self.street_ways(h.get("cross_street") or "")
        if not a or not b:
            return None
        best = None
        for la in a[1]:
            for pa in la:
                d = polylines_distance(pa, b[1])
                if best is None or d < best[0]:
                    best = (d, pa)
        if best is None or best[0] > 80:
            # "между X и Y": streets parallel, use the gap between them.
            return None
        return {"kind": "point", "lat": best[1][0], "lon": best[1][1],
                "radius_m": RADIUS["intersection"], "label": f"{a[0]} / {b[0]}"}

    def resolve_street(self, h: dict) -> dict | None:
        found = self.street_ways(h.get("street") or "")
        if not found:
            return None
        name, lines = found
        mid = polylines_midpoint(lines)
        return {"kind": "street", "lat": mid[0], "lon": mid[1],
                "radius_m": polylines_length(lines) / 2, "geometry": lines, "label": name}

    def resolve_named(self, name: str | None, kind: str) -> dict | None:
        if not name:
            return None
        res = self.nominatim(q=f"{name}, София")
        if not res:
            return None
        r = res[0]
        radius = RADIUS[kind]
        if kind == "area" and r.get("boundingbox"):
            s, n, w, e = map(float, r["boundingbox"])
            radius = min(1500, max(radius, haversine((s, w), (n, e)) / 2))
        return {"kind": "point" if kind == "landmark" else "area", "lat": float(r["lat"]),
                "lon": float(r["lon"]), "radius_m": radius, "label": r.get("display_name", "")}

    def resolve(self, h: dict) -> dict | None:
        t = h.get("type")
        try:
            if t == "address":
                return self.resolve_address(h) or self.resolve_street(h)
            if t == "intersection":
                return self.resolve_intersection(h) or self.resolve_street(h)
            if t == "street":
                return self.resolve_street(h)
            if t == "landmark":
                if not h.get("landmark") and h.get("street"):
                    return self.resolve_street(h)
                return self.resolve_named(h.get("landmark"), "landmark")
            if t == "area":
                return self.resolve_named(h.get("area") or h.get("landmark"), "area")
        except httpx.HTTPError as e:
            log.warning("geocoding %s failed: %s", h, e)
        return None


# ---------------------------------------------------------------- pipeline

def target_point(conn, cfg: dict, geo: Geocoder) -> tuple[float, float]:
    t = cfg["target"]
    if t.get("lat") and t.get("lon"):
        return float(t["lat"]), float(t["lon"])
    m = re.match(r"(.*?)\s+(\d+\w?)\s*,", t["address"])
    if m:
        hit = geo.resolve_address({"street": m.group(1), "number": m.group(2)})
        if hit:
            return hit["lat"], hit["lon"]
    res = geo.nominatim(q=t["address"])
    if not res:
        raise RuntimeError(f"cannot geocode target {t['address']!r}; set target.lat/lon in config.toml")
    return float(res[0]["lat"]), float(res[0]["lon"])


def ordered_hints(row, extraction: dict | None) -> list[dict]:
    hints = list((extraction or {}).get("location_hints") or [])
    # The seller's drop-down street is always available as a last resort.
    if row["street_hint"]:
        hints.append({"type": "street", "street": row["street_hint"], "confidence": 0.3,
                      "evidence": f"drop-down street: {row['street_hint']}"})
    return sorted(
        (h for h in hints if h.get("type") in PRIORITY),
        key=lambda h: (float(h.get("confidence") or 0) < 0.25, PRIORITY[h["type"]],
                       -float(h.get("confidence") or 0)),
    )


def geocode_all(conn, cfg: dict, force: bool = False) -> dict:
    geo = Geocoder(conn, cfg)
    target = target_point(conn, cfg, geo)
    stats = {"located": 0, "unlocated": 0, "skipped": 0, "target": target}
    rows = conn.execute(
        """SELECT l.*, e.data AS extraction, e.input_hash AS ehash
           FROM listings l LEFT JOIN extractions e ON e.listing_id = l.id
           WHERE l.active = 1"""
    ).fetchall()
    for row in rows:
        ehash = hashlib.sha1(
            json.dumps([row["ehash"], row["street_hint"], target]).encode()
        ).hexdigest()
        existing = conn.execute(
            "SELECT extraction_hash FROM locations WHERE listing_id = ?", (row["id"],)
        ).fetchone()
        if existing and existing["extraction_hash"] == ehash and not force:
            stats["skipped"] += 1
            continue

        extraction = json.loads(row["extraction"]) if row["extraction"] else None
        loc, used = None, None
        for h in ordered_hints(row, extraction):
            loc = geo.resolve(h)
            if loc:
                used = h
                break

        if loc:
            p = (loc["lat"], loc["lon"])
            dist_exp = haversine(target, p)
            if loc["kind"] == "street":
                dist_min = polylines_distance(target, loc["geometry"])
            else:
                dist_min = max(0.0, dist_exp - loc["radius_m"])
            confidence = float(used.get("confidence") or 0.3)
            label = f"{loc['label']} ({used['type']}: {used.get('evidence', '')})"
            stats["located"] += 1
        else:
            p, dist_exp, dist_min, confidence, label = (None, None), None, None, 0.0, "unknown"
            loc = {"kind": "none", "radius_m": None}
            stats["unlocated"] += 1

        conn.execute(
            """INSERT OR REPLACE INTO locations (listing_id, extraction_hash, kind, lat, lon, radius_m,
                   geometry, label, confidence, dist_expected_m, dist_min_m, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["id"], ehash, loc["kind"], p[0], p[1], loc["radius_m"],
             json.dumps(loc.get("geometry")) if loc.get("geometry") else None,
             label, confidence, dist_exp, dist_min, now()),
        )
        conn.commit()
    stats["requests"] = geo.requests
    stats["cache_hits"] = geo.cache_hits
    return stats
