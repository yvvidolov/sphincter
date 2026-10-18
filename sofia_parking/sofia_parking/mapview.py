"""Folium map of ranked listings around the target address.

Folium draws the base map and the target; listings are drawn by our own Leaflet JS from embedded JSON,
so the side list and the map can react to each other (hover highlights, click shows the listing).
"""

import json
import math
from pathlib import Path

import folium

LOCATED_KINDS = ("point", "street", "area")
# Padding around a street's extent when drawing its accuracy ellipse.
ELLIPSE_PAD_M = 40


def color(score: float) -> str:
    if score >= 70:
        return "#1a9850"
    if score >= 55:
        return "#91cf60"
    if score >= 40:
        return "#fc8d59"
    return "#d73027"


def fmt_price(p) -> str:
    return f"{p:,.0f} €".replace(",", " ") if p else "n/a"


def short_address(it: dict) -> str:
    """'160, Цар Симеон, Център, София, … (address: …)' -> 'Цар Симеон 160'."""
    base = (it["loc_label"] or "").split(" (")[0]
    parts = [p.strip() for p in base.split(",") if p.strip()]
    if len(parts) > 1 and parts[0][:1].isdigit():
        return f"{parts[1]} {parts[0]}"
    return parts[0] if parts else (it["street_hint"] or "")


def street_ellipse(lines: list[list[list[float]]], n: int = 48) -> list[list[float]] | None:
    """Oriented ellipse around all points of a street's polylines (principal axes of the points)."""
    pts = [p for line in lines for p in line]
    if len(pts) < 2:
        return None
    lat0 = sum(p[0] for p in pts) / len(pts)
    lon0 = sum(p[1] for p in pts) / len(pts)
    ky, kx = 111_320.0, 111_320.0 * math.cos(math.radians(lat0))
    xy = [((p[1] - lon0) * kx, (p[0] - lat0) * ky) for p in pts]
    sxx = sum(x * x for x, _ in xy)
    syy = sum(y * y for _, y in xy)
    sxy = sum(x * y for x, y in xy)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    u = (math.cos(theta), math.sin(theta))
    v = (-u[1], u[0])
    a = max(abs(x * u[0] + y * u[1]) for x, y in xy) + ELLIPSE_PAD_M
    b = max(abs(x * v[0] + y * v[1]) for x, y in xy) + ELLIPSE_PAD_M
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = a * math.cos(t) * u[0] + b * math.sin(t) * v[0]
        y = a * math.cos(t) * u[1] + b * math.sin(t) * v[1]
        out.append([round(lat0 + y / ky, 6), round(lon0 + x / kx, 6)])
    return out


def big_photo(url: str) -> str:
    """List-card thumbnail .../266/<id>_sz.jpg -> full size .../266/big/<id>_sz.jpg."""
    head, _, name = url.rpartition("/")
    return f"{head}/big/{name}"


def item_json(rank: int, it: dict) -> dict:
    ex = it["extraction"]
    located = it["loc_kind"] in LOCATED_KINDS and it["lat"] is not None
    lines = it["geometry"] if it["loc_kind"] == "street" and it["geometry"] else None
    return {
        "rank": rank,
        "id": it["id"],
        "url": it["url"],
        "score": it["score"],
        "color": color(it["score"]),
        "price": it["price_eur"],
        "priceText": fmt_price(it["price_eur"]),
        "vat": it["vat_note"] or "",
        "priceDrop": fmt_price(it["price_drop"]) if it["price_drop"] else "",
        "area": it["area_m2"],
        "floor": it["floor"] or "",
        "kind": it["kind"] or "",
        "parkingType": ex.get("parking_type") or "",
        "address": short_address(it),
        "locLabel": it["loc_label"] or "",
        "conf": it["loc_conf"] or 0,
        "dist": it["distance_m"],
        "listedAt": it["listed_at"].strftime("%Y-%m-%d"),
        "listedHow": it["listed_how"],
        "ageDays": round(it["age_days"], 1),
        "fresh": it["is_fresh"],
        "isNew": it["is_new"],
        "firstSeen": it["first_seen"][:10],
        "summary": ex.get("summary_en") or "",
        "flags": ex.get("red_flags") or [],
        "description": it["description"] or it["snippet"] or "",
        "photos": [{"thumb": u, "big": big_photo(u)} for u in json.loads(it["photos"] or "[]")],
        "lat": it["lat"] if located else None,
        "lon": it["lon"] if located else None,
        "radius": it["radius_m"] if located and not lines else None,
        "lines": lines,
        "ellipse": street_ellipse(lines) if lines else None,
    }


CSS = """
<style>
.sp-side{position:fixed;top:10px;left:10px;bottom:10px;width:min(430px,calc(100vw - 20px));z-index:9999;
  display:flex;flex-direction:column;background:#fff;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.3);
  font:12px/1.35 sans-serif;color:#222}
.sp-head{padding:8px;border-bottom:1px solid #ddd;display:flex;gap:6px 10px;align-items:center;flex-wrap:wrap}
.sp-list{overflow:auto;flex:1}
.sp-row{display:grid;grid-template-columns:22px 26px 1fr auto;gap:2px 6px;padding:5px 8px;
  border-bottom:1px solid #eee;cursor:pointer;position:relative}
.sp-row:hover,.sp-row.hl{background:#fff4c2}
.sp-row.sel{background:#ffe680}
.sp-row.unloc{opacity:.7}
.sp-row.hidden{opacity:.45;background:repeating-linear-gradient(45deg,#fff,#fff 6px,#f2f2f2 6px,#f2f2f2 12px)}
.sp-row .rk{color:#666}
.sp-row .sc{font-weight:bold}
.sp-row .addr{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sp-row .sub{grid-column:3/5;color:#555}
.sp-row .dt{color:#666;text-align:right;white-space:nowrap}
.sp-row .hide{position:absolute;right:6px;bottom:4px;display:none;font-size:11px;padding:1px 6px;cursor:pointer;
  border:1px solid #bbb;border-radius:3px;background:#fff}
.sp-row:hover .hide{display:block}
.sp-badge{display:inline-block;padding:0 4px;border-radius:3px;font-size:10px;font-weight:bold;color:#fff;
  background:#1a9850;margin-left:3px}
.sp-badge.fresh{background:#7b3294}
.sp-badge.hid{background:#777}
/* List on the left, detail on the right, so the middle of the map stays visible. */
.leaflet-top.leaflet-left,.leaflet-bottom.leaflet-left{left:calc(min(430px,100vw - 20px) + 10px)}
.sp-detail{position:fixed;top:10px;bottom:10px;right:10px;width:min(520px,calc(100vw - 480px));z-index:9999;display:none;flex-direction:column;background:#fff;
  border-radius:6px;box-shadow:0 1px 8px rgba(0,0,0,.35);font:14px/1.5 sans-serif;color:#222}
.sp-detail.open{display:flex}
.sp-dhead{display:flex;align-items:flex-start;gap:8px;padding:10px 12px;border-bottom:1px solid #ddd}
.sp-dhead .title{flex:1;font-size:17px;font-weight:bold;line-height:1.3}
.sp-dhead .title small{display:block;font-size:13px;font-weight:normal;color:#555}
.sp-btn{font:13px sans-serif;padding:5px 10px;border:1px solid #bbb;border-radius:4px;background:#fff;
  cursor:pointer;color:#222;text-decoration:none;white-space:nowrap}
.sp-btn:hover{background:#f0f0f0}
.sp-btn.close{font-size:20px;line-height:1;padding:4px 11px}
.sp-dbody{overflow:auto;padding:10px 12px 14px}
.sp-facts{color:#444;margin-bottom:8px}
.sp-photos{display:flex;gap:6px;overflow-x:auto;margin-bottom:10px}
.sp-photos img{height:200px;border-radius:4px;border:1px solid #ccc;background:#eee}
.sp-desc{white-space:pre-wrap;font-size:15px;line-height:1.6}
.sp-ai{margin-top:14px;padding:8px 10px;border-left:3px solid #9aa;background:#f6f8f8;color:#555;font-size:12.5px}
.sp-ai b{color:#333}
.sp-pin{width:26px;height:26px;border-radius:50%;border:2px solid #222;color:#000;font:bold 11px/22px sans-serif;
  text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.5);transition:transform .1s}
.sp-pin.fresh{border:3px solid #7b3294;line-height:20px}
.sp-pin.hl{transform:scale(1.45);box-shadow:0 0 0 3px #ffd400,0 1px 4px rgba(0,0,0,.6)}
@media (max-width:1000px){
  .sp-detail{left:10px;right:10px;width:auto;top:auto;height:60vh;z-index:10000}
}
</style>
"""

PANEL = """
<div class="sp-side">
  <div class="sp-head">
    <b>Ranking</b> <span id="sp-count"></span>
    <label>sort <select id="sp-sort">
      <option value="score">score</option><option value="date">newest</option>
      <option value="price">price</option><option value="dist">distance</option>
    </select></label>
    <label>within <select id="sp-dist">
      <option value="">any</option><option value="500">0.5 km</option><option value="1000">1 km</option>
      <option value="1500">1.5 km</option><option value="2000">2 km</option>
    </select></label>
    <label><input type="checkbox" id="sp-fresh"> only fresh (&le; __FRESH__ d)</label>
    <label><input type="checkbox" id="sp-showhidden"> show hidden (<span id="sp-nhidden">0</span>)</label>
  </div>
  <div class="sp-list" id="sp-list"></div>
</div>
<div class="sp-detail" id="sp-detail"></div>
"""

JS = r"""
window.addEventListener("DOMContentLoaded", function () {
  const map = __MAP__;
  const DATA = __DATA__;
  const STORE = "sofia-parking:";
  let selected = null;

  // Hidden listings and view settings live in this browser's localStorage (survives map regeneration).
  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(STORE + key)) ?? fallback; } catch (e) { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem(STORE + key, JSON.stringify(value)); } catch (e) { /* private mode etc. */ }
  }
  const hidden = new Set(load("hidden", []));

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  const km = d => d == null ? "?" : (d / 1000).toFixed(2) + " km";
  const ago = d => d < 1 ? "today" : Math.round(d) + " d ago";

  // Accuracy shapes go below the pins and ignore the mouse so they never steal clicks.
  map.createPane("sp-acc").style.zIndex = 390;
  map.createPane("sp-street").style.zIndex = 395;

  DATA.forEach(it => {
    if (it.lat == null) return;
    const accStyle = {pane: "sp-acc", color: it.color, weight: 1, opacity: .5, fillColor: it.color,
                      fillOpacity: .1, interactive: false};
    if (it.ellipse) it.acc = L.polygon(it.ellipse, accStyle);
    else if (it.radius > 50) it.acc = L.circle([it.lat, it.lon], {radius: it.radius, ...accStyle});
    if (it.lines) it.street = L.polyline(it.lines, {pane: "sp-street", color: it.color, weight: 7, opacity: .85,
                                                   interactive: false});
    const icon = L.divIcon({
      className: "", iconSize: [26, 26], iconAnchor: [13, 13],
      html: `<div class="sp-pin${it.fresh ? " fresh" : ""}" style="background:${it.color}">${it.rank}</div>`,
    });
    it.marker = L.marker([it.lat, it.lon], {icon, zIndexOffset: 1000 - it.rank, riseOnHover: true})
      .bindTooltip(`#${it.rank} ${it.priceText} · ${it.address}`, {direction: "top", offset: [0, -14]})
      .on("mouseover", () => highlight(it, true))
      .on("mouseout", () => highlight(it, false))
      .on("click", () => toggle(it, false));
  });

  function setOnMap(it, on) {
    for (const layer of [it.marker, it.acc]) {
      if (!layer) continue;
      if (on && !map.hasLayer(layer)) layer.addTo(map);
      if (!on && map.hasLayer(layer)) layer.remove();
    }
    if (!on && it.street) it.street.remove();
  }

  function highlight(it, on) {
    on = on || it === selected;
    const pin = it.marker && it.marker.getElement() && it.marker.getElement().firstChild;
    if (pin) pin.classList.toggle("hl", on);
    if (it.marker) it.marker.setZIndexOffset(on ? 5000 : 1000 - it.rank);
    if (it.street) on && map.hasLayer(it.marker) ? it.street.addTo(map) : it.street.remove();
    if (it.acc) it.acc.setStyle({weight: on ? 3 : 1, opacity: on ? 1 : .5, fillOpacity: on ? .25 : .1});
    if (it.row) it.row.classList.toggle("hl", on);
  }

  // Clicking the selected listing again, the close button, Esc or an empty spot on the map deselects.
  function toggle(it, pan) { select(it === selected ? null : it, pan); }

  function select(it, pan) {
    const prev = selected;
    selected = it;
    if (prev) { prev.row && prev.row.classList.remove("sel"); highlight(prev, false); }
    if (!it) { detail.classList.remove("open"); return; }
    highlight(it, true);
    if (it.row) { it.row.classList.add("sel"); it.row.scrollIntoView({block: "nearest"}); }
    showDetail(it);
    if (pan && it.lat != null) panIntoView(it);
  }

  // Keep the user's zoom; only pan when the pin is hidden behind a panel or off screen.
  function panIntoView(it) {
    const size = map.getSize();
    const left = document.querySelector(".sp-side").getBoundingClientRect().right + 20;
    const d = detail.getBoundingClientRect();
    const right = d.width && d.left > left ? d.left - 20 : size.x - 20;
    const top = 40, bottom = d.width && d.left <= left ? d.top - 20 : size.y - 40;  // detail docked at the bottom
    const pt = map.latLngToContainerPoint([it.lat, it.lon]);
    if (pt.x >= left && pt.x <= right && pt.y >= top && pt.y <= bottom) return;
    map.panBy([pt.x - (left + right) / 2, pt.y - (top + bottom) / 2]);
  }
  map.on("click", () => select(null));
  document.addEventListener("keydown", e => { if (e.key === "Escape") select(null); });

  function setHidden(it, on) {
    on ? hidden.add(it.id) : hidden.delete(it.id);
    save("hidden", [...hidden]);
    if (on && it === selected && !showHiddenBox.checked) select(null);
    render();
    if (it === selected) showDetail(it);
  }

  const detail = document.getElementById("sp-detail");
  function showDetail(it) {
    detail.replaceChildren();
    const head = el("div", "sp-dhead");
    const title = el("div", "title", `#${it.rank} · ${it.address || "location unknown"} · ${it.priceText}`);
    title.append(el("small", null, [`score ${it.score}`, `${km(it.dist)} from target`, it.vat].filter(Boolean).join(" · ")));
    const hideBtn = el("button", "sp-btn", hidden.has(it.id) ? "Unhide" : "Hide");
    hideBtn.onclick = () => setHidden(it, !hidden.has(it.id));
    const link = el("a", "sp-btn", "imot.bg ↗");
    link.href = it.url; link.target = "_blank";
    const close = el("button", "sp-btn close", "×");
    close.title = "Close (Esc)";
    close.onclick = () => select(null);
    head.append(title, hideBtn, link, close);

    const body = el("div", "sp-dbody");
    let facts = `${it.kind} · ${it.area || "?"} m² · ${it.floor} · ${it.listedHow} ${it.listedAt} (${ago(it.ageDays)})`;
    if (it.priceDrop) facts += ` · price dropped by ${it.priceDrop}`;
    body.append(el("div", "sp-facts", facts));
    if (it.photos.length) {
      const strip = el("div", "sp-photos");
      it.photos.forEach(ph => {
        const a = el("a");
        a.href = ph.big; a.target = "_blank";
        const img = el("img");
        img.src = ph.big; img.loading = "lazy"; img.alt = "photo";
        img.onerror = () => { if (img.src !== ph.thumb) img.src = ph.thumb; };
        a.append(img);
        strip.append(a);
      });
      body.append(strip);
    }
    body.append(el("div", "sp-desc", it.description || "(no description)"));

    const ai = el("div", "sp-ai");
    const line = (label, text) => {
      if (!text) return;
      const d = el("div");
      d.append(el("b", null, label + ": "), document.createTextNode(text));
      ai.append(d);
    };
    line("AI summary", it.summary);
    line("Red flags", it.flags.length ? it.flags.join(", ") : "none");
    line("Type", it.parkingType);
    line("Location guess", `${it.locLabel} (confidence ${it.conf.toFixed(2)})`);
    line("First seen by us", it.firstSeen);
    body.append(ai);

    detail.append(head, body);
    detail.classList.add("open");
    body.scrollTop = 0;
  }

  const list = document.getElementById("sp-list");
  const sortSel = document.getElementById("sp-sort");
  const distSel = document.getElementById("sp-dist");
  const freshBox = document.getElementById("sp-fresh");
  const showHiddenBox = document.getElementById("sp-showhidden");
  const sorters = {
    score: (a, b) => a.rank - b.rank,
    date: (a, b) => a.ageDays - b.ageDays,
    price: (a, b) => (a.price || 1e12) - (b.price || 1e12),
    dist: (a, b) => (a.dist ?? 1e12) - (b.dist ?? 1e12),
  };
  const view = load("view", {});
  if (view.sort in sorters) sortSel.value = view.sort;
  if ([...distSel.options].some(o => o.value === view.maxDist)) distSel.value = view.maxDist;
  freshBox.checked = !!view.fresh;
  showHiddenBox.checked = !!view.showHidden;

  function render() {
    save("view", {sort: sortSel.value, maxDist: distSel.value, fresh: freshBox.checked,
                  showHidden: showHiddenBox.checked});
    const maxDist = distSel.value ? +distSel.value : null;
    const shown = DATA.filter(it => (!freshBox.checked || it.fresh)
                                   && (maxDist == null || (it.dist != null && it.dist <= maxDist))
                                   && (showHiddenBox.checked || !hidden.has(it.id)))
                      .sort(sorters[sortSel.value]);
    const nHidden = DATA.filter(it => hidden.has(it.id)).length;
    document.getElementById("sp-count").textContent = `(${shown.length}/${DATA.length})`;
    document.getElementById("sp-nhidden").textContent = nHidden;
    const visible = new Set(shown);
    DATA.forEach(it => setOnMap(it, visible.has(it)));
    if (selected && !visible.has(selected)) select(null);

    list.replaceChildren();
    shown.forEach(it => {
      const isHidden = hidden.has(it.id);
      const r = el("div", "sp-row" + (it.lat == null ? " unloc" : "") + (it === selected ? " sel" : "")
                          + (isHidden ? " hidden" : ""));
      r.append(el("span", "rk", "#" + it.rank));
      const sc = el("span", "sc", Math.round(it.score));
      sc.style.color = it.color;
      r.append(sc);
      const addr = el("span", "addr", it.address || "location unknown");
      addr.title = it.locLabel;
      r.append(addr);
      const dt = el("span", "dt", it.listedAt.slice(5));
      dt.title = `${it.listedHow} ${it.listedAt}`;
      if (it.fresh) dt.append(el("span", "sp-badge fresh", "FRESH"));
      if (it.isNew) dt.append(el("span", "sp-badge", "NEW"));
      if (isHidden) dt.append(el("span", "sp-badge hid", "HIDDEN"));
      r.append(dt);
      let sub = `${it.priceText} · ${it.area || "?"} m² · ${km(it.dist)} · ${it.kind}`;
      if (it.priceDrop) sub += ` · ↓${it.priceDrop}`;
      r.append(el("span", "sub", sub));
      const hb = el("button", "hide", isHidden ? "unhide" : "hide");
      hb.onclick = e => { e.stopPropagation(); setHidden(it, !isHidden); };
      r.append(hb);
      r.onmouseenter = () => highlight(it, true);
      r.onmouseleave = () => highlight(it, false);
      r.onclick = () => toggle(it, true);
      it.row = r;
      list.append(r);
    });
    if (selected) highlight(selected, true);
  }
  sortSel.onchange = distSel.onchange = freshBox.onchange = showHiddenBox.onchange = render;
  render();
});
"""


def build(items: list[dict], target: tuple[float, float], target_label: str, path: str | Path,
          tiles: str, tiles_attr: str | None = None, fresh_days: float = 7) -> Path:
    m = folium.Map(location=target, zoom_start=15, tiles=tiles, attr=tiles_attr or None)
    folium.Marker(
        target, tooltip=target_label, icon=folium.Icon(color="blue", icon="home")
    ).add_to(m)
    folium.Circle(target, radius=500, color="#3182bd", weight=1, fill=False, dash_array="4").add_to(m)
    folium.Circle(target, radius=1000, color="#3182bd", weight=1, fill=False, dash_array="4").add_to(m)

    data = json.dumps([item_json(rank, it) for rank, it in enumerate(items, 1)], ensure_ascii=False)
    data = data.replace("</", "<\\/")  # never close the <script> early
    root = m.get_root()
    root.header.add_child(folium.Element(CSS))
    root.html.add_child(folium.Element(PANEL.replace("__FRESH__", f"{fresh_days:g}")))
    root.script.add_child(folium.Element(JS.replace("__MAP__", m.get_name()).replace("__DATA__", data)))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(path))
    return path
