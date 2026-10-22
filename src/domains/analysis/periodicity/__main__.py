"""Isolated worker entry point for OVITO analysis."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from . import CandidatePolicy, analyze_periodicity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text()) if args.metadata else {}
    policy = CandidatePolicy(**json.loads(args.policy.read_text())) if args.policy else CandidatePolicy()
    result = analyze_periodicity(args.input, args.output, source_metadata=metadata, policy=policy, cache_dir=args.cache)
    joint = result["multiscale_analysis"]
    print(json.dumps({"status":result["status"], "multiscale_analysis_status":joint["status"],
                      "summary_image_path":result["summary_image_path"],
                      "total_candidate_count":joint["total_candidate_count"],
                      "reason":joint.get("reason"),
                      "output":str(args.output.resolve()), "elapsed_seconds":result["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
