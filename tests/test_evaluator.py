"""DeepEval smoke test for the response evaluator.

Ported from the branch's responsible_ai/test_evaluator.py. Asserts
FaithfulnessMetric on a simple case. This is a starting point — the full suite
(Faithfulness across 10 cases + a custom GEval "HiLRespect" + eval_cases.jsonl)
is tracked as separate Responsible-AI work.

Run:  python -m tests.test_evaluator      (or: pytest tests/test_evaluator.py)
"""
from __future__ import annotations

from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase


def build_case() -> LLMTestCase:
    return LLMTestCase(
        input="I used Python and TensorFlow.",
        actual_output="Candidate demonstrates Python and TensorFlow experience.",
        retrieval_context=["I used Python and TensorFlow."],
    )


def test_faithfulness() -> None:
    metric = FaithfulnessMetric()
    score = metric.measure(build_case())
    print("Faithfulness Score:", score)
    assert score is not None


if __name__ == "__main__":
    test_faithfulness()
