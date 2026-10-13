"""The original adds 1; Create must revise it."""
import json
from pathlib import Path


def values(ctx):
    payload = json.loads(Path(ctx.knob("values_path")).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(type(value) is not int for value in payload):
        raise ValueError("values_path must contain a JSON array of exact integers")
    return payload


def adjust(ctx):
    ctx.write_json("adjusted.json", {"values": [value + 1 for value in values(ctx)]})


def identity(ctx):
    ctx.write_json("identity.json", {"values": values(ctx)})
