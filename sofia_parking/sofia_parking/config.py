import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "target": {
        "address": "ул. <STREET> <NUMBER>, София",
        "lat": None,
        "lon": None,
    },
    "scrape": {
        "list_url": "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya/tsentar/garazh-parkomyasto",
        "max_pages": 10,
        "delay_min": 4.0,
        "delay_max": 9.0,
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
        ),
        "detail_refresh_days": 30,
    },
    "llm": {
        "backend": "litellm",
        "model": "anthropic/claude-sonnet-5-5",
        "api_base": "",
        "api_key_env": "",
        "temperature": 0.0,
        "concurrency": 4,
        "claude_model": "sonnet",
        "claude_lean": False,
        "claude_extra_args": [],
    },
    "geocode": {
        "nominatim_url": "https://nominatim.openstreetmap.org",
        "overpass_url": [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        ],
        "user_agent": "sofia-parking/0.1 (personal use)",
        "bbox": [42.665, 23.290, 42.715, 23.365],
    },
    "score": {
        "max_distance_m": 2500,
        "weight_distance": 0.6,
        "weight_price": 0.3,
        "weight_confidence": 0.1,
    },
    "output": {
        "db_path": "data/parking.sqlite",
        "map_path": "data/map.html",
        # tile.openstreetmap.org blocks pages opened from file:// (no Referer) and CARTO now
        # needs an API key, so default to Esri's keyless street map.
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        "tiles_attr": "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, OpenStreetMap contributors",
    },
}


def load(path: str | Path = "config.toml") -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path)
    if path.exists():
        with path.open("rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            cfg.setdefault(section, {}).update(values)
    return cfg
