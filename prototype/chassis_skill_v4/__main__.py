"""Offline tool entry point; intentionally no API or credential imports."""
import argparse
import json
from pathlib import Path
from .decision import EvidenceSession, compare_candidates


def main():
    parser = argparse.ArgumentParser(description="V4 local engineering decision-support patch")
    parser.add_argument("--input", required=True, help="JSON: revisions[{case, policy, starting_spec?, candidate_ids?}]")
    parser.add_argument("--output", required=True, help="New local JSON output; cannot overwrite inputs/results")
    args = parser.parse_args()
    target = Path(args.output).resolve()
    if target.exists():
        parser.error("output_already_exists")
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    session, results = EvidenceSession(), []
    for revision in payload["revisions"]:
        evidence = session.assess(revision["case"], revision.get("candidate_ids"))
        comparison = (compare_candidates(revision["case"], evidence, revision["policy"],
                                         revision.get("starting_spec"))
                      if evidence.get("case_hash") else None)
        results.append({"evidence": evidence, "comparison": comparison})
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump({"revisions": results, "work": dict(session.counts),
                   "api_calls": 0, "status": "offline_tool_result"},
                  handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"status": "written", "revision_count": len(results), "api_calls": 0}))


if __name__ == "__main__":
    main()
