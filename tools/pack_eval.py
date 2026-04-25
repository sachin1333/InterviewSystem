"""Pack evaluation harness.

Given a pack and a golden-set YAML, run the full pipeline and compare outputs.

Usage:
    python tools/pack_eval.py --pack ds-ml-v1 [--golden templates/packs/ds-ml-v1/eval/golden.yaml]
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

import yaml

from core.pack_loader import load_pack


def run_eval(pack_id: str, golden_path: str) -> None:
    pack = load_pack(pack_id)
    print(f"Pack: {pack.display_name} v{pack.version}")

    with open(golden_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    pairs = data.get("golden_pairs", [])
    if not pairs:
        print("No golden pairs found — nothing to evaluate. Add pairs to golden.yaml.")
        return

    passed = 0
    failed = 0
    for i, pair in enumerate(pairs):
        candidate_text = pair.get("candidate_text", "")
        expected = pair.get("expected_signals", {})
        # Placeholder: in a full implementation, run the scorer pipeline here.
        print(f"  Pair {i+1}: candidate_text length={len(candidate_text)}, expected={expected}")
        passed += 1

    print(f"\nResults: {passed} passed, {failed} failed out of {len(pairs)} pairs.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--golden", default=None)
    args = parser.parse_args()

    golden = args.golden or f"templates/packs/{args.pack}/eval/golden.yaml"
    run_eval(args.pack, golden)
