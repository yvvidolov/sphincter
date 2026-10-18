"""Folium map of ranked listings around the target address."""

import html
from pathlib import Path

import folium

LOCATED_KINDS = ("point", "street", "area")


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


def popup_html(rank: int, it: dict) -> str:
    ex = it["extraction"]
    dist = f"{it['distance_m']:.0f} m" if it["distance_m"] is not None else "unknown"
    flags = ", ".join(ex.get("red_flags") or []) or "none"
    tags = []
    if it["is_new"]:
        tags.append("<b style='color:#1a9850'>NEW</b>")
    if it["price_drop"]:
        tags.append(f"<b style='color:#1a9850'>-{fmt_price(it['price_drop'])}</b>")
    e = html.escape
    return f"""
<div style="width:320px;font-family:sans-serif;font-size:13px">
  <b>#{rank} · score {it['score']}</b> {' '.join(tags)}<br>
  <b>{e(it['kind'] or '')}</b> · {fmt_price(it['price_eur'])} {e(it['vat_note'] or '')}<br>
  {it['area_m2'] or '?'} m² · {e(it['floor'] or '')} · {e(ex.get('parking_type') or '')}<br>
  Distance: {dist} · location confidence {it['loc_conf'] or 0:.2f}<br>
  <i>{e(it['loc_label'] or '')}</i><br>
  {e(ex.get('summary_en') or '')}<br>
  Red flags: {e(flags)}<br>
  <a href="{e(it['url'])}" target="_blank">open on imot.bg</a>
</div>"""


def build(items: list[dict], target: tuple[float, float], target_label: str, path: str | Path,
          tiles: str, tiles_attr: str | None = None) -> Path:
    m = folium.Map(location=target, zoom_start=15, tiles=tiles, attr=tiles_attr or None)
    folium.Marker(
        target, tooltip=target_label, icon=folium.Icon(color="blue", icon="home")
    ).add_to(m)
    folium.Circle(target, radius=500, color="#3182bd", weight=1, fill=False, dash_array="4").add_to(m)
    folium.Circle(target, radius=1000, color="#3182bd", weight=1, fill=False, dash_array="4").add_to(m)

    top = folium.FeatureGroup(name="Top 10", show=True).add_to(m)
    rest = folium.FeatureGroup(name="Other listings", show=True).add_to(m)

    for rank, it in enumerate(items, 1):
        if it["loc_kind"] not in LOCATED_KINDS:
            continue
        layer = top if rank <= 10 else rest
        c = color(it["score"])
        body = popup_html(rank, it)
        # A folium Popup can only be attached once; reusing it breaks the generated JS.
        def popup():
            return folium.Popup(body, max_width=360)
        tooltip = f"#{rank} {fmt_price(it['price_eur'])} · {it['score']}"
        if it["loc_kind"] == "street" and it["geometry"]:
            for line in it["geometry"]:
                folium.PolyLine(line, color=c, weight=6, opacity=0.6, popup=popup(), tooltip=tooltip).add_to(layer)
        elif it["radius_m"] and it["radius_m"] > 50:
            folium.Circle((it["lat"], it["lon"]), radius=it["radius_m"], color=c, weight=1,
                          fill=True, fill_opacity=0.12).add_to(layer)
        folium.CircleMarker(
            (it["lat"], it["lon"]), radius=8, color="#333", weight=1, fill=True,
            fill_color=c, fill_opacity=0.9, popup=popup(), tooltip=tooltip,
        ).add_to(layer)
        folium.map.Marker(
            (it["lat"], it["lon"]),
            icon=folium.DivIcon(html=f"<div style='font-size:10px;font-weight:bold;"
                                     f"transform:translate(-4px,-7px)'>{rank}</div>"),
        ).add_to(layer)

    rows = []
    for rank, it in enumerate(items, 1):
        dist = f"{it['distance_m'] / 1000:.2f} km" if it["distance_m"] is not None else "?"
        badge = " 🆕" if it["is_new"] else ""
        badge += " ↓" if it["price_drop"] else ""
        rows.append(
            f"<tr><td>{rank}</td><td style='color:{color(it['score'])};font-weight:bold'>{it['score']:.0f}</td>"
            f"<td>{fmt_price(it['price_eur'])}</td><td>{it['area_m2'] or '?'}</td><td>{dist}</td>"
            f"<td><a href='{html.escape(it['url'])}' target='_blank'>{html.escape(it['kind'] or 'link')}</a>{badge}</td></tr>"
        )
    panel = f"""
<div style="position:fixed;top:10px;right:10px;z-index:9999;background:white;max-height:85vh;overflow:auto;
            padding:8px;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.3);font:12px sans-serif">
  <b>Ranking</b> ({len(items)} active)
  <table style="border-collapse:collapse" cellpadding="2">
    <tr><th>#</th><th>Score</th><th>Price</th><th>m²</th><th>Dist</th><th></th></tr>
    {''.join(rows)}
  </table>
</div>"""
    m.get_root().html.add_child(folium.Element(panel))
    folium.LayerControl(position="topleft").add_to(m)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(path))
    return path
