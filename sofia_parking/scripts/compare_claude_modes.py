"""Compare extraction quality of the default vs lean claude-cli modes.

Runs every active listing through: default mode once more (A2) and lean mode twice (L1, L2).
The existing extraction in the DB is A1 (must have been made in default mode).
Default-vs-default and lean-vs-lean agreement give the run-to-run noise floor; the
cross-mode pairs show whether lean mode differs more than that.

Raw runs are saved to data/compare/, so re-running with --analyze-only makes no LLM calls.
"""

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sofia_parking import config, db, extract, geocode  # noqa: E402

OUT = Path("data/compare")
FIELDS = ["is_genuine_listing", "fits_regular_car", "parking_type", "standalone_title",
          "price_includes_vat", "sold_with_apartment_only", "construction_stage", "level"]


def run_mode(cfg, rows, lean: bool, name: str):
    path = OUT / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    c = json.loads(json.dumps(cfg))
    c["llm"]["claude_lean"] = lean

    def one(row):
        t = time.monotonic()
        env = extract.run_claude_cli(c, extract.SYSTEM, extract.build_prompt(row))
        u = env["usage"]
        try:
            data = extract.parse_json(env["result"])
        except Exception as e:
            data = {"_error": str(e)}
        return row["id"], {
            "data": data,
            "seconds": time.monotonic() - t,
            "cost_usd": env.get("total_cost_usd"),
            "input_tokens": u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"],
            "cache_creation": u["cache_creation_input_tokens"],
            "output_tokens": u["output_tokens"],
        }

    with ThreadPoolExecutor(max_workers=cfg["llm"]["concurrency"]) as pool:
        res = dict(pool.map(one, rows))
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def locate(geo, target, row, data):
    for h in geocode.ordered_hints(row, data):
        loc = geo.resolve(h)
        if loc:
            p = (loc["lat"], loc["lon"])
            return {"p": p, "kind": loc["kind"], "hint": h["type"], "dist": geocode.haversine(target, p)}
    return None


def spearman(a, b):
    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0] * len(x)
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    return 1 - 6 * sum((x - y) ** 2 for x, y in zip(ra, rb)) / (n * (n * n - 1))


def compare(name, ids, rows, runs, locs, x, y):
    same_place, moved, dist_x, dist_y = 0, [], [], []
    field_agree = {f: 0 for f in FIELDS}
    for lid in ids:
        lx, ly = locs[x][lid], locs[y][lid]
        if lx and ly:
            d = geocode.haversine(lx["p"], ly["p"])
            moved.append(d)
            same_place += d < 100
            dist_x.append(lx["dist"])
            dist_y.append(ly["dist"])
        elif not lx and not ly:
            same_place += 1
        else:
            moved.append(float("inf"))
        dx, dy = runs[x][lid]["data"], runs[y][lid]["data"]
        for f in FIELDS:
            field_agree[f] += dx.get(f) == dy.get(f)
    n = len(ids)
    finite = [m for m in moved if m != float("inf")]
    return {
        "pair": name,
        "same_place_<100m": f"{same_place}/{n}",
        "moved_>500m": sum(m > 500 for m in moved),
        "median_shift_m": round(statistics.median(finite)) if finite else None,
        "dist_rank_spearman": round(spearman(dist_x, dist_y), 3) if len(dist_x) > 2 else None,
        **{f: f"{v}/{n}" for f, v in field_agree.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    conn = db.connect(cfg["output"]["db_path"])
    rows = conn.execute(
        """SELECT l.*, e.data AS a1 FROM listings l JOIN extractions e ON e.listing_id = l.id
           WHERE l.active = 1 AND e.model NOT LIKE '%:lean'"""
    ).fetchall()
    OUT.mkdir(parents=True, exist_ok=True)

    runs = {"A1": {r["id"]: {"data": json.loads(r["a1"])} for r in rows}}
    if not args.analyze_only or (OUT / "A2.json").exists():
        runs["A2"] = run_mode(cfg, rows, lean=False, name="A2")
        runs["L1"] = run_mode(cfg, rows, lean=True, name="L1")
        runs["L2"] = run_mode(cfg, rows, lean=True, name="L2")

    geo = geocode.Geocoder(conn, cfg)
    target = geocode.target_point(conn, cfg, geo)
    by_id = {r["id"]: r for r in rows}
    ids = sorted(by_id)
    locs = {k: {lid: locate(geo, target, by_id[lid], v[lid]["data"]) for lid in ids} for k, v in runs.items()}

    print("## Cost per listing")
    for k in ("A2", "L1", "L2"):
        v = runs[k].values()
        mean = lambda key: statistics.mean(x[key] for x in v)  # noqa: E731
        print(f"{k}: input {mean('input_tokens'):.0f} tok (cache writes {mean('cache_creation'):.0f}), "
              f"output {mean('output_tokens'):.0f} tok, {mean('seconds'):.1f} s, ${mean('cost_usd'):.4f}, "
              f"parse errors {sum('_error' in x['data'] for x in v)}")

    print("\n## Agreement")
    pairs = [("A1 vs A2 (default noise)", "A1", "A2"), ("L1 vs L2 (lean noise)", "L1", "L2"),
             ("A1 vs L1", "A1", "L1"), ("A2 vs L2", "A2", "L2"), ("A1 vs L2", "A1", "L2"), ("A2 vs L1", "A2", "L1")]
    table = [compare(n, ids, rows, runs, locs, x, y) for n, x, y in pairs]
    cols = list(table[0])
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for t in table:
        print("| " + " | ".join(str(t[c]) for c in cols) + " |")

    print("\n## Listings whose location differs between modes (>300 m) but not within a mode")
    for lid in ids:
        L = locs
        def d(a, b):
            if not L[a][lid] or not L[b][lid]:
                return 0 if L[a][lid] is L[b][lid] else float("inf")
            return geocode.haversine(L[a][lid]["p"], L[b][lid]["p"])
        if d("A1", "A2") < 300 and d("L1", "L2") < 300 and d("A1", "L1") > 300:
            def desc(k):
                x = L[k][lid]
                return f"{x['hint']}->{x['dist']:.0f}m" if x else "none"
            print(f"{lid}: A1 {desc('A1')}, L1 {desc('L1')} | {(by_id[lid]['description'] or '')[:120]!r}")


if __name__ == "__main__":
    main()
