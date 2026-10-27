"""Private offline scoring; results never return to design controllers."""
from .decision import digest, physical_case


def score_stage(task, index, snapshot, usage):
    target = task["stages"][index]["private"]["target"]
    ref = task["stages"][index]["private"]["reference"]
    final = snapshot.get("final")
    candidate_id = final["candidate_id"] if final else None
    adopted = final["adopted_state"] if final else snapshot["state"]
    same_inputs = digest(physical_case(adopted["case"])) == digest(physical_case(target["case"]))
    same_policy = digest(adopted["policy"]) == digest(target["policy"])
    engineering = ref["engineering"]["candidates"].get(candidate_id)
    eligible = ref["quality"]["eligible"].get(candidate_id)
    valid = bool(final and same_inputs and same_policy and eligible)
    preferred = bool(valid and candidate_id in ref["quality"]["best_ids"])
    return {"stage": index, "submitted": bool(final), "valid": valid, "preferred": preferred,
            "input_consistent": same_inputs, "policy_consistent": same_policy,
            "physically_feasible": bool(engineering and engineering["feasible"]),
            "metrics": eligible["values"] if eligible else None,
            "terminal": snapshot["terminal"], **usage}


def score_run(task, job, stages, usage):
    return {**job, "family": task["family"], "category": task["category"],
            "stages": stages, "all_stages_valid": len(stages) == 3 and all(x["valid"] for x in stages),
            "all_stages_preferred": len(stages) == 3 and all(x["preferred"] for x in stages),
            "stage_valid_rate": sum(x["valid"] for x in stages) / 3,
            "preference_rate": sum(x["preferred"] for x in stages) / 3, **usage}
