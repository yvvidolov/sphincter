"""Polite imot.bg scraper: list pages every run, detail pages only when something changed."""

import hashlib
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from .db import now

log = logging.getLogger(__name__)

BGN_PER_EUR = 1.95583


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_price(text: str) -> float | None:
    """'49 000 €' -> 49000.0; BGN prices are converted to EUR."""
    m = re.search(r"([\d\s.,]+)\s*(€|EUR|лв|BGN|\$|USD)", text.replace("\xa0", " "))
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    if not digits:
        return None
    value = float(digits)
    if m.group(2) in ("лв", "BGN"):
        value /= BGN_PER_EUR
    return round(value, 2)


def parse_area(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|m2|m²|м2)", text)
    return float(m.group(1).replace(",", ".")) if m else None


def listing_id_from_url(url: str) -> str | None:
    m = re.search(r"obiava-([0-9a-z]+)-", url)
    return m.group(1) if m else None


def parse_list_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for item in soup.select("div.item[id^=ida]"):
        link = item.select_one("a.title") or item.select_one("a.saveSlink")
        if not link or not link.get("href"):
            continue
        url = link["href"]
        if url.startswith("//"):
            url = "https:" + url
        lid = item["id"].removeprefix("ida")

        kind = ""
        if link.contents and isinstance(link.contents[0], str):
            kind = link.contents[0].strip().removeprefix("Продава").strip()

        price_div = item.select_one("div.price")
        price_text = _text(price_div.find("div")) if price_div else ""
        vat_note = _text(price_div.find("span")) if price_div else ""
        trend = ""
        if price_div:
            trend = " ".join(c for c in price_div.get("class", []) if c != "price")

        snippet = _text(item.select_one("div.info"))
        # Snippet starts with "<area>, <street>, <floor>, <description...>"
        parts = [p.strip() for p in snippet.split(",")]
        street = next(
            (p for p in parts[:3] if re.match(r"(ул\.|бул\.|пл\.|ж\.к\.|кв\.|пл |ул |бул )", p)),
            "",
        )
        floor = next((p for p in parts[:4] if re.search(r"(ниво|етаж|Партер|Сутерен)", p, re.I)), "")

        cards.append(
            {
                "id": lid,
                "url": url,
                "kind": kind,
                "price_text": price_text,
                "price_eur": parse_price(price_text),
                "price_trend": trend,
                "vat_note": vat_note,
                "area_m2": parse_area(snippet),
                "street_hint": street,
                "floor": floor,
                "snippet": snippet,
                "seller": _text(item.select_one("div.seller div.name")),
                "photos": photo_urls(item, lid),
            }
        )
    return cards


def photo_urls(soup, lid: str) -> list[str]:
    """Thumbnail URLs of a listing's photos. Their file names contain the listing id, which tells them
    apart from icons and from photos of other (recommended) listings; full-size ones live under /big/."""
    photos = []
    for img in soup.select("img"):
        for src in (img.get("data-src"), img.get("src")):
            if not src or "photosimotbg" not in src or lid not in src:
                continue
            src = re.sub(r"/+big/", "/", "https:" + src if src.startswith("//") else src)
            if src not in photos:
                photos.append(src)
    return photos


def has_next_page(html: str, page: int) -> bool:
    return re.search(rf'href="[^"]*/p-{page + 1}"', html) is not None


def parse_detail_page(html: str, lid: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    params = {}
    for div in soup.select("div.adParams > div"):
        label = div.contents[0].strip().rstrip(":") if div.contents and isinstance(div.contents[0], str) else ""
        params[label] = _text(div.find("strong"))
    loc = soup.select_one("div.info div.location")
    street = _text(loc.find("div")) if loc else ""
    info = soup.select_one("div.adPrice div.info")
    modified = _text(info.find("div")) if info else ""
    views_el = info.find("span") if info else None
    views = int(re.sub(r"\D", "", views_el.get_text())) if views_el and re.search(r"\d", views_el.get_text()) else None
    desc_el = soup.select_one("div.moreInfo div.text")
    description = desc_el.get_text("\n", strip=True) if desc_el else ""
    features = [_text(d) for d in soup.select("div.carExtri div.items > div")]
    return {
        "description": description,
        "features": features,
        "street": street,
        "area_m2": parse_area(params.get("Площ", "").replace("m 2", "m2").replace("m2", " кв.м")),
        "floor": params.get("Етаж", ""),
        "params": params,
        "modified_text": modified,
        "views": views,
        "photos": photo_urls(soup, lid) if lid else [],
    }


def card_hash(card: dict) -> str:
    key = json.dumps(
        [card["kind"], card["price_text"], card["snippet"], card["street_hint"]], ensure_ascii=False
    )
    return hashlib.sha1(key.encode()).hexdigest()


class Client:
    def __init__(self, cfg: dict):
        s = cfg["scrape"]
        self.delay = (s["delay_min"], s["delay_max"])
        self.http = httpx.Client(
            headers={
                "User-Agent": s["user_agent"],
                "Accept-Language": "bg,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=30,
            follow_redirects=True,
        )
        self._last = 0.0
        self.requests = 0
        self.bytes = 0

    def get(self, url: str) -> str:
        wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
        if self._last and wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                self.requests += 1
                r = self.http.get(url)
                self._last = time.monotonic()
                self.bytes += len(r.content)
                if r.status_code in (429, 503):
                    raise httpx.HTTPStatusError("throttled", request=r.request, response=r)
                r.raise_for_status()
                # imot.bg serves windows-1251 without a charset in Content-Type.
                m = re.search(r"charset=([\w-]+)", r.headers.get("content-type", ""))
                return r.content.decode(m.group(1) if m else "cp1251", errors="replace")
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise
                backoff = 30 * (attempt + 1)
                log.warning("GET %s failed (%s), retrying in %ss", url, e, backoff)
                time.sleep(backoff)
        raise RuntimeError("unreachable")


def scrape(conn, cfg: dict) -> dict:
    client = Client(cfg)
    base = cfg["scrape"]["list_url"].rstrip("/")
    seen: dict[str, dict] = {}
    complete = False
    for page in range(1, cfg["scrape"]["max_pages"] + 1):
        url = base if page == 1 else f"{base}/p-{page}"
        html = client.get(url)
        cards = parse_list_page(html)
        log.info("page %d: %d listings", page, len(cards))
        for c in cards:
            seen.setdefault(c["id"], c)
        if not cards or not has_next_page(html, page):
            complete = True
            break

    ts = now()
    stats = {"seen": len(seen), "new": 0, "changed": 0, "price_changed": 0, "gone": 0, "details": 0}
    to_fetch = []
    refresh_before = (
        datetime.now(timezone.utc) - timedelta(days=cfg["scrape"]["detail_refresh_days"])
    ).isoformat()

    for c in seen.values():
        h = card_hash(c)
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (c["id"],)).fetchone()
        if row is None:
            stats["new"] += 1
            conn.execute(
                """INSERT INTO listings (id, url, kind, price_eur, price_text, price_trend, vat_note,
                       area_m2, floor, street_hint, snippet, seller, card_hash, first_seen, last_seen, active,
                       photos)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (c["id"], c["url"], c["kind"], c["price_eur"], c["price_text"], c["price_trend"],
                 c["vat_note"], c["area_m2"], c["floor"], c["street_hint"], c["snippet"], c["seller"],
                 h, ts, ts, json.dumps(c["photos"])),
            )
            conn.execute("INSERT INTO price_history VALUES (?,?,?)", (c["id"], c["price_eur"], ts))
            to_fetch.append(c)
            continue

        if row["price_eur"] != c["price_eur"]:
            stats["price_changed"] += 1
            conn.execute("INSERT INTO price_history VALUES (?,?,?)", (c["id"], c["price_eur"], ts))
        changed = row["card_hash"] != h
        stats["changed"] += changed
        conn.execute(
            """UPDATE listings SET url=?, kind=?, price_eur=?, price_text=?, price_trend=?, vat_note=?,
                   area_m2=COALESCE(area_m2, ?), floor=COALESCE(NULLIF(floor, ''), ?), street_hint=?,
                   snippet=?, seller=?, card_hash=?, last_seen=?, active=1,
                   photos=COALESCE(NULLIF(photos, '[]'), ?)
               WHERE id=?""",
            (c["url"], c["kind"], c["price_eur"], c["price_text"], c["price_trend"], c["vat_note"],
             c["area_m2"], c["floor"], c["street_hint"], c["snippet"], c["seller"], h, ts,
             json.dumps(c["photos"]), c["id"]),
        )
        if changed or not row["detail_fetched_at"] or row["detail_fetched_at"] < refresh_before:
            to_fetch.append(c)
    conn.commit()

    if complete and seen:
        placeholders = ",".join("?" * len(seen))
        cur = conn.execute(
            f"UPDATE listings SET active=0 WHERE active=1 AND id NOT IN ({placeholders})",
            list(seen),
        )
        stats["gone"] = cur.rowcount
        conn.commit()

    for c in to_fetch:
        try:
            d = parse_detail_page(client.get(c["url"]), c["id"])
        except httpx.HTTPError as e:
            log.warning("detail %s failed: %s", c["id"], e)
            continue
        conn.execute(
            """UPDATE listings SET description=?, features=?, modified_text=?, views=?,
                   photos=COALESCE(NULLIF(?, '[]'), photos),
                   area_m2=COALESCE(?, area_m2), floor=COALESCE(NULLIF(?, ''), floor),
                   street_hint=COALESCE(NULLIF(?, ''), street_hint), detail_fetched_at=?
               WHERE id=?""",
            (d["description"], json.dumps(d["features"], ensure_ascii=False), d["modified_text"],
             d["views"], json.dumps(d["photos"]), d["area_m2"], d["floor"], d["street"], now(), c["id"]),
        )
        conn.commit()
        stats["details"] += 1
        log.info("detail %s fetched", c["id"])
    stats["requests"] = client.requests
    stats["kb"] = round(client.bytes / 1024)
    return stats
