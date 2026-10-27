import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from itertools import product
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(p)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def flatten(value, prefix=""):
    if isinstance(value, dict) and value:
        return {p: v for k, x in value.items() for p, v in flatten(x, f"{prefix}.{k}" if prefix else k).items()}
    if isinstance(value, list) and value:
        return {p: v for i, x in enumerate(value) for p, v in flatten(x, f"{prefix}.{i}").items()}
    return {prefix: value}


def get(obj, path, default=None):
    try:
        for part in path.split("."):
            obj = obj[int(part)] if isinstance(obj, list) else obj[part]
        return obj
    except (KeyError, IndexError, TypeError, ValueError):
        return default


def put(obj, path, value):
    parts = path.split(".")
    target = obj
    for i, part in enumerate(parts[:-1]):
        if isinstance(target, list):
            target = target[int(part)]
        else:
            if part not in target:
                target[part] = [] if parts[i + 1].isdigit() else {}
            target = target[part]
    if isinstance(target, list):
        target[int(parts[-1])] = deepcopy(value)
    else:
        target[parts[-1]] = deepcopy(value)


def remove(obj, path):
    parts = path.split(".")
    parent = get(obj, ".".join(parts[:-1])) if len(parts) > 1 else obj
    if isinstance(parent, dict):
        parent.pop(parts[-1], None)


def candidates(case):
    """Catalog enumeration only: contains no engineering feasibility information."""
    c = case["illustrative_catalog"]
    if case.get("design_family") == "dual_power_stream":
        for dp, dm, sp, sm in product(c["drive"]["pump_displacements_ml_rev"],
                c["drive"]["motor_displacements_ml_rev"], c["steering"]["pump_displacements_ml_rev"],
                c["steering"]["motor_displacements_ml_rev"]):
            yield f"DP{dp:g}_DM{dm:g}_SP{sp:g}_SM{sm:g}", {
                "drive_pump_ml_rev": dp, "drive_motor_ml_rev": dm,
                "steering_pump_ml_rev": sp, "steering_motor_ml_rev": sm}
    else:
        for p, m in product(c["pump_displacements_ml_rev"], c["motor_displacements_ml_rev"]):
            yield f"P{p:g}_M{m:g}", {"pump_ml_rev": p, "motor_ml_rev": m}
