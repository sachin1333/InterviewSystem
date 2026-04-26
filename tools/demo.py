"""
End-to-end demo of the InterviewSystem using the HTTP app and Starlette TestClient.

Demonstrates a full session flow:
  1. Create session
  2. GET question
  3. POST answers to advance FSM
  4. Get final result and print feedback

Run with:
  cd /sessions/affectionate-brave-johnson/mnt/InterviewSystem
  FAKE_LLM=1 .venv-sandbox/bin/python tools/demo.py
"""
from __future__ import annotations

import os
import re
import tempfile
import textwrap
from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_insight_interp_scorer import LlmInsightInterpScorer
from adapters.scorer.llm_problem_framing_scorer import LlmProblemFramingScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Dimension
from core.rubric_loader import load_rubric

# Rubric with 4 dimensions
_RUBRIC = textwrap.dedent("""\
    name: ds-ml-engineer
    version: 1
    dimensions:
      - name: problem_framing
        weight: 0.25
      - name: model_rationale
        weight: 0.25
      - name: insight_interp
        weight: 0.25
      - name: communication
        weight: 0.25
    aggregation:
      min_signals_per_dimension: 1
      confidence_weighting: true
""")

_SCORED_DIMENSIONS = (
    Dimension.problem_framing,
    Dimension.model_rationale,
    Dimension.insight_interp,
    Dimension.communication,
)

# Demo answers
_ANSWERS = [
    "The key is to understand the problem domain and identify the right metrics. "
    "For churn prediction, I'd focus on F1 or recall depending on business costs.",
    
    "I would start with a baseline logistic regression model to establish interpretability. "
    "Then explore tree-based models like random forest or XGBoost for better performance.",
    
    "The trade-off between bias and variance is critical. Simple models have high bias but low variance, "
    "while complex models have low bias but high variance. Cross-validation helps us find the sweet spot.",
]


def demo() -> None:
    """Run a full 3-turn interview session."""
    print("=" * 80)
    print("InterviewSystem E2E Demo (FakeRouter)")
    print("=" * 80)
    print()
    
    # Set up temporary directory for logs and outputs
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        log_path = tmp_path / "log.db"
        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        
        # Initialize adapters
        log = SqliteEventLog(log_path)
        router = ModelRouter(FakeRouter())
        rubric = load_rubric(yaml_str=_RUBRIC)
        aggregator = RubricAggregator(rubric, output_dir=output_dir)
        
        runner = SessionRunner(
            challenger=LlmChallenger(router),
            scorers={
                Dimension.problem_framing: LlmProblemFramingScorer(router),
                Dimension.model_rationale: LlmRationaleScorer(router),
                Dimension.insight_interp: LlmInsightInterpScorer(router),
                Dimension.communication: LlmCommunicationScorer(router),
            },
            aggregator=aggregator,
            scored_dimensions=_SCORED_DIMENSIONS,
        )
        
        # Create FastAPI app and TestClient (with redirects enabled)
        app = make_app(
            log=log,
            runner=runner,
            target_answers=3,
            max_probes=0,
            rubric_version="ds-ml-engineer@1",
            output_dir=output_dir,
        )
        client = TestClient(app, follow_redirects=True)
        
        # Step 1: Create session
        print("[1] POST /sessions → Create session")
        resp = client.post("/sessions", data={"candidate_handle": "demo-candidate"})
        print(f"    Status: {resp.status_code}")
        
        # Extract session_id from redirect chain
        session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
        print(f"    Session ID: {session_id}")
        print()
        
        # Step 2-4: Three-turn loop
        for turn_num, answer in enumerate(_ANSWERS, 1):
            # GET the question
            print(f"[{1+turn_num*2-1}] GET /sessions/{session_id} → Get question")
            resp = client.get(f"/sessions/{session_id}")
            print(f"    Status: {resp.status_code}")
            
            # Extract question text from the HTML form
            match = re.search(r'Generated prompt ([a-f0-9]+)', resp.text)
            if match:
                q_text = f"Generated prompt {match.group(1)}"
            else:
                q_text = "(question not found in response)"
            
            print(f"    Question: {q_text}")
            print()
            
            # POST answer
            print(f"[{1+turn_num*2}] POST /sessions/{session_id}/turn → Submit answer {turn_num}")
            print(f"    Answer: {answer}")
            resp = client.post(
                f"/sessions/{session_id}/turn",
                data={
                    "answer": answer,
                    "code": "",
                    "turn_nonce": f"nonce-{turn_num}",
                }
            )
            print(f"    Status: {resp.status_code}")
            print()
        
        # Step 5: Get result page
        print(f"[8] GET /sessions/{session_id}/result → Get final score and feedback")
        resp = client.get(f"/sessions/{session_id}/result")
        print(f"    Status: {resp.status_code}")
        print()
        
        # Parse score from response
        composite_match = re.search(r'Composite score: ([\d.]+)', resp.text)
        if composite_match:
            composite_score = float(composite_match.group(1))
            print(f"    Composite Score: {composite_score:.2f}")
            
            # Extract per-dimension scores
            dim_matches = re.findall(r'<li>([^:]+): ([\d.]+)</li>', resp.text)
            if dim_matches:
                print("    Per-Dimension Scores:")
                for dim, score in dim_matches:
                    print(f"      - {dim}: {float(score):.2f}")
        else:
            print("    (Score not yet computed)")
        
        print()
        
        # Look for feedback file
        feedback_files = list(output_dir.glob(f"{session_id}_feedback.md"))
        if feedback_files:
            print("[9] Read feedback markdown")
            feedback_text = feedback_files[0].read_text()
            print()
            print("=" * 80)
            print("CANDIDATE FEEDBACK")
            print("=" * 80)
            print(feedback_text)
        else:
            print("(No feedback file written)")
        
        print()
        print("=" * 80)
        print("Demo Complete")
        print("=" * 80)


if __name__ == "__main__":
    # Ensure FAKE_LLM is set
    os.environ["FAKE_LLM"] = "1"
    demo()
