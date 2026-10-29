"""Same synthetic fixtures against baseline and offline candidate; no model API."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate.store import CandidateStore
from scripts.benchmark_baseline import quality


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix="tracememory-candidate-comparison-") as temporary:
    base, candidate = Path(temporary) / "baseline", Path(temporary) / "candidate"
    base.mkdir()
    candidate.mkdir()
    report = {"official_evaluation": False, "model_calls": 0,
              "baseline": quality(base), "offline_candidate": quality(candidate, CandidateStore),
              "limitation": "Seven known synthetic diagnostics; no real VLM/embedding efficacy or generalization claim."}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
