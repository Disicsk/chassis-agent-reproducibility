"""Offline evaluation. Never imported by the design controllers."""
from .util import flatten, get
from .runtime import same


def score(task, state, usage):
    final = state.get("final")
    expected = task["private"]["target"]
    used = final["case"] if final else state["case"]
    required = task["private"]["requirement_paths"]
    matched = [p for p in required if same(get(used, p), get(expected, p))]
    # Full physical input equality in addition to the interpretable requirement checklist.
    physical = {p: v for p, v in flatten(expected).items() if p.startswith(
        ("requirements.", "scenarios.", "planetary.", "efficiencies.", "design_limits.", "illustrative_catalog.", "design_family"))}
    wrong = [p for p, v in physical.items() if not same(get(used, p), v)]
    cid = final.get("candidate_id") if final else None
    candidate = task["private"]["reference"]["candidates"].get(cid)
    feasible = bool(candidate and candidate["feasible"])
    valid = bool(final and not wrong and feasible)
    return {
        "task_id": task["id"], "family": task["family"], "category": task["category"],
        "method": state["method"], "valid_design": valid, "submitted": bool(final),
        "candidate_feasible_for_confirmed_input": feasible, "input_mismatch_paths": wrong,
        "requirement_coverage": len(matched) / len(required),
        "revision_consistent": (bool(final and not wrong) if task["category"] == "S3" else None),
        "question_rounds": state["question_rounds"],
        "repeated_confirmation_fields": state["repeated_confirmation_fields"],
        "terminal": state["terminal"], "counts": state["counts"], **usage,
        "evaluation_scope": "final choice and attached input, independent frozen engineering reference; prose correctness requires semantic audit"}
