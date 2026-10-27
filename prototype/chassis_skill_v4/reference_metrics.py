"""Offline evaluation reference, never imported by the model-facing patch.

Engineering checks use independently written equations. Preference metrics and
ordering are reimplemented here, without importing decision.py.
"""
from ..process_comparison.reference import reference


def expected_choices(case, priority, minimum_margin, starting_values=None):
    numeric = reference(case)
    specifications = [row["spec"] for row in numeric["candidates"].values()]
    maxima = [max(parts[i] for parts in specifications)
              for i in range(len(specifications[0]))]
    outcomes = {}
    for cid, row in numeric["candidates"].items():
        margin = min(
            (check["available"] - check["required"]) / check["available"]
            for scenario in row["checks"].values() for name, check in scenario.items()
            if name in ("pressure", "flow") or name.endswith(("_pressure", "_flow"))
        )
        if not row["feasible"] or margin + 1e-10 < minimum_margin:
            continue
        values = {
            "hydraulic_margin": margin,
            "size_index": sum(value / scale for value, scale in zip(row["spec"], maxima))
                          / len(maxima),
            "changed_spec_positions": (sum(a != b for a, b in zip(row["spec"], starting_values))
                                       if starting_values is not None else None),
        }
        key = tuple(round(-values[p] if p == "hydraulic_margin" else values[p], 10)
                    for p in priority)
        outcomes[cid] = {"values": values, "key": key}
    best = min((row["key"] for row in outcomes.values()), default=None)
    return {"eligible": outcomes,
            "best_ids": sorted(cid for cid, row in outcomes.items() if row["key"] == best)}
