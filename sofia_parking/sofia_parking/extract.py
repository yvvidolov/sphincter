"""LLM extraction of location hints and parameters from listing text.

The LLM only reads text and returns structured hints (streets, numbers, landmarks).
Turning hints into coordinates is the job of geocode.py.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from .db import now

log = logging.getLogger(__name__)

PROMPT_VERSION = "2"

SYSTEM = """You analyse Bulgarian real-estate listings for parking spaces and garages in central Sofia.
Extract facts ONLY from the given text. Never invent coordinates or addresses.
Answer with a single JSON object and nothing else."""

INSTRUCTIONS = """Listing data:
{listing}

Return JSON with exactly these keys:
{{
  "location_hints": [            // most specific first; empty list if the text has nothing
    {{
      "type": "address" | "intersection" | "street" | "landmark" | "area",
      "street": "street/boulevard name in Bulgarian without the 'ул.'/'бул.' prefix, or null",
      "number": "building number as written, or null",
      "cross_street": "second street for intersections or 'между X и Y', or null",
      "landmark": "landmark / building / complex / square / metro station name, or null",
      "area": "neighbourhood or area name (e.g. 'Сточна гара', 'Оборище'), or null",
      "confidence": 0.0-1.0,     // how sure the parking spot is at this place
      "evidence": "short exact quote from the text"
    }}
  ],
  "is_genuine_listing": true | false,   // false for spam, ads for other products, wanted/buying ads
  "fits_regular_car": true | false | null,   // false if only for a motorbike/storage
  "parking_type": "underground_garage" | "ground_garage" | "underground_spot" | "covered_spot" | "open_spot" | "yard_spot" | "lift_spot" | "other" | "unknown",
  "level": "e.g. -1, -2, ground, or null",
  "area_m2": number or null,
  "standalone_title": true | false | null,   // самостоятелен обект / own notary deed, not just right of use
  "construction_stage": "e.g. Акт 14, Акт 15, Акт 16, existing, or null",
  "price_includes_vat": true | false | null,
  "sold_with_apartment_only": true | false | null,
  "features": ["short English tags, e.g. automatic door, security, lift, EV charger"],
  "red_flags": ["short English notes: right of use only, tandem spot, mechanical parking, needs apartment, auction, unclear location, etc."],
  "summary_en": "one sentence summary in English"
}}

Notes:
- The "street" field on imot.bg is chosen from a drop-down by the seller and is often only approximate;
  prefer streets/numbers written in the description, but include the drop-down street as a lower-confidence hint.
- 'в района на X', 'до X', 'срещу X', 'зад X' are landmark/area hints; keep the landmark name only.
"""


def listing_payload(row) -> dict:
    return {
        "type": row["kind"],
        "price": row["price_text"],
        "vat_note": row["vat_note"],
        "area_m2": row["area_m2"],
        "floor": row["floor"],
        "street_dropdown": row["street_hint"],
        "description": row["description"] or row["snippet"],
        "features": json.loads(row["features"]) if row["features"] else [],
    }


def input_hash(payload: dict, model: str) -> str:
    key = json.dumps([PROMPT_VERSION, model, payload], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in LLM output: {text[:200]!r}")
    # raw_decode ignores anything the model wrote after the object.
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def call_litellm(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    import litellm

    llm = cfg["llm"]
    kwargs = {}
    if llm.get("api_base"):
        kwargs["api_base"] = llm["api_base"]
    if llm.get("api_key_env"):
        kwargs["api_key"] = os.environ[llm["api_key_env"]]
    resp = litellm.completion(
        model=llm["model"],
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=llm["temperature"],
        **kwargs,
    )
    try:
        cost = litellm.completion_cost(completion_response=resp)
    except Exception:  # unknown model pricing, e.g. behind a proxy
        cost = 0.0
    usage = {"in": resp.usage.prompt_tokens, "out": resp.usage.completion_tokens, "cost": cost}
    return resp.choices[0].message.content, usage


def call_claude_cli(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    out = run_claude_cli(cfg, system, prompt)
    u = out.get("usage") or {}
    usage = {
        "in": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        + u.get("cache_read_input_tokens", 0),
        "out": u.get("output_tokens", 0),
        # API-equivalent cost; a subscription isn't billed per call.
        "cost": out.get("total_cost_usd") or 0.0,
    }
    return out["result"], usage


def run_claude_cli(cfg: dict, system: str, prompt: str) -> dict:
    """Use the local Claude Code CLI in headless mode (billed to the Claude subscription).
    Returns the CLI's JSON envelope (result text, usage, cost)."""
    llm = cfg["llm"]
    cmd = ["claude", "-p", "--output-format", "json", "--model", llm["claude_model"]]
    if llm.get("claude_lean"):
        # Replace Claude Code's system prompt and drop tools/MCP/skills/settings:
        # ~550 input tokens of overhead per call instead of ~19k.
        cmd += [
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources", "",
            "--no-session-persistence",
        ]
    else:
        cmd += ["--append-system-prompt", system]
    cmd += llm.get("claude_extra_args", [])
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip() or proc.stdout.strip()}")
    out = json.loads(proc.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude CLI error: {out.get('result')}")
    return out


def model_name(cfg: dict) -> str:
    llm = cfg["llm"]
    if llm["backend"] != "claude-cli":
        return llm["model"]
    return f"claude-cli:{llm['claude_model']}" + (":lean" if llm.get("claude_lean") else "")


def complete(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    if cfg["llm"]["backend"] == "claude-cli":
        return call_claude_cli(cfg, system, prompt)
    return call_litellm(cfg, system, prompt)


def build_prompt(row) -> str:
    return INSTRUCTIONS.format(listing=json.dumps(listing_payload(row), ensure_ascii=False, indent=1))


def extract_one(cfg: dict, row) -> tuple[dict, dict]:
    text, usage = complete(cfg, SYSTEM, build_prompt(row))
    return parse_json(text), usage


def extract_all(conn, cfg: dict, force: bool = False) -> dict:
    model = model_name(cfg)
    stats = {"extracted": 0, "skipped": 0, "failed": 0, "llm_calls": 0, "tokens_in": 0, "tokens_out": 0,
             "cost_usd": 0.0}
    todo = []
    for row in conn.execute("SELECT * FROM listings WHERE active = 1").fetchall():
        h = input_hash(listing_payload(row), model)
        existing = conn.execute(
            "SELECT input_hash FROM extractions WHERE listing_id = ?", (row["id"],)
        ).fetchone()
        if existing and existing["input_hash"] == h and not force:
            stats["skipped"] += 1
        else:
            todo.append((row, h))

    with ThreadPoolExecutor(max_workers=cfg["llm"]["concurrency"]) as pool:
        futures = {pool.submit(extract_one, cfg, row): (row, h) for row, h in todo}
        for fut in as_completed(futures):
            row, h = futures[fut]
            stats["llm_calls"] += 1
            try:
                data, usage = fut.result()
            except Exception as e:  # keep going; the next run retries
                log.warning("extraction failed for %s: %s", row["id"], e)
                stats["failed"] += 1
                continue
            stats["tokens_in"] += usage["in"]
            stats["tokens_out"] += usage["out"]
            stats["cost_usd"] = round(stats["cost_usd"] + usage["cost"], 4)
            _store(conn, row, h, model, data)
            stats["extracted"] += 1
    return stats


def _store(conn, row, h, model, data):
    conn.execute(
        "INSERT OR REPLACE INTO extractions (listing_id, input_hash, model, data, created_at) "
        "VALUES (?,?,?,?,?)",
        (row["id"], h, model, json.dumps(data, ensure_ascii=False), now()),
    )
    conn.commit()
    log.info("extracted %s: %s", row["id"], data.get("summary_en", ""))
