import argparse
import json
import logging

from . import config, db, extract, geocode, mapview, score, scraper


def cmd_scrape(conn, cfg, args):
    print("scrape:", scraper.scrape(conn, cfg))


def cmd_extract(conn, cfg, args):
    print("extract:", extract.extract_all(conn, cfg, force=args.force))


def cmd_geocode(conn, cfg, args):
    print("geocode:", geocode.geocode_all(conn, cfg, force=args.force))


def _target(conn, cfg):
    return geocode.target_point(conn, cfg, geocode.Geocoder(conn, cfg))


def cmd_map(conn, cfg, args):
    items = score.ranked(conn, cfg)
    path = mapview.build(items, _target(conn, cfg), cfg["target"]["address"], cfg["output"]["map_path"],
                         tiles=cfg["output"]["tiles"], tiles_attr=cfg["output"]["tiles_attr"])
    print(f"map: {path.resolve()}")


def cmd_report(conn, cfg, args):
    items = score.ranked(conn, cfg)[: args.limit]
    print(f"{'#':>3} {'score':>5} {'price €':>9} {'m²':>5} {'dist':>7}  {'type':<11} location")
    for i, it in enumerate(items, 1):
        dist = f"{it['distance_m']:.0f}m" if it["distance_m"] is not None else "?"
        flags = (" NEW" if it["is_new"] else "") + (f" -{it['price_drop']:.0f}€" if it["price_drop"] else "")
        price = f"{it['price_eur']:,.0f}" if it["price_eur"] else "n/a"
        print(f"{i:>3} {it['score']:>5.1f} {price:>9} {it['area_m2'] or 0:>5.0f} {dist:>7}  "
              f"{(it['kind'] or ''):<11} {(it['loc_label'] or '')[:70]}{flags}")
        print(f"{'':>10}{it['url']}")


def cmd_show(conn, cfg, args):
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"no listing {args.id}")
    print(json.dumps(dict(row), ensure_ascii=False, indent=1))
    for table in ("extractions", "locations"):
        r = conn.execute(f"SELECT * FROM {table} WHERE listing_id = ?", (args.id,)).fetchone()
        if r:
            print(f"--- {table}")
            d = dict(r)
            if "data" in d:
                d["data"] = json.loads(d["data"])
            d.pop("geometry", None)
            print(json.dumps(d, ensure_ascii=False, indent=1))


def cmd_run(conn, cfg, args):
    cmd_scrape(conn, cfg, args)
    if not args.no_llm:
        cmd_extract(conn, cfg, args)
    cmd_geocode(conn, cfg, args)
    cmd_map(conn, cfg, args)
    cmd_report(conn, cfg, args)


def main():
    p = argparse.ArgumentParser(prog="sofia-parking")
    p.add_argument("-c", "--config", default="config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scrape", help="fetch list pages and changed detail pages")
    for name in ("extract", "geocode"):
        sp = sub.add_parser(name)
        sp.add_argument("--force", action="store_true", help="redo even if inputs are unchanged")
    sub.add_parser("map", help="write the HTML map")
    rp = sub.add_parser("report", help="print the ranking")
    rp.add_argument("-n", "--limit", type=int, default=20)
    sp = sub.add_parser("show", help="dump everything known about one listing")
    sp.add_argument("id")
    rp = sub.add_parser("run", help="scrape + extract + geocode + map + report")
    rp.add_argument("--no-llm", action="store_true", help="skip LLM extraction (use drop-down street only)")
    rp.add_argument("--force", action="store_true")
    rp.add_argument("-n", "--limit", type=int, default=20)

    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config.load(args.config)
    conn = db.connect(cfg["output"]["db_path"])
    globals()[f"cmd_{args.cmd}"](conn, cfg, args)


if __name__ == "__main__":
    main()
