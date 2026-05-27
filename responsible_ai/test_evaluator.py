from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

test_case = LLMTestCase(
    input="I used Python and TensorFlow.",
    
    actual_output="""
    Candidate demonstrates Python and TensorFlow experience.
    """
)

metric = FaithfulnessMetric()

score = metric.measure(test_case)

print("Faithfulness Score:", score)