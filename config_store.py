import json
from pathlib import Path

CONFIG_FILE = Path("user_config.json")


def save_config(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def load_config():
    if not CONFIG_FILE.exists():
        return None
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))