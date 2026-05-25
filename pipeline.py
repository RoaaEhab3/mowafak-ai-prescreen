from agents.response_evaluator import ResponseEvaluator
from whisper_stt import WhisperSTT


def main():

    stt = WhisperSTT()

    result = stt.transcribe_audio("sample-speech-1m.wav")

    print("\n========== TRANSCRIPT ==========\n")
    print(result["transcript"])

    evaluator = ResponseEvaluator()

    assessment = evaluator.evaluate(
        question="What is this audio about?",
        transcript=result["transcript"],
        skills=["summary", "clarity"]
    )

    print("\n========== ASSESSMENT ==========\n")
    print(assessment.model_dump_json(indent=4))


if __name__ == "__main__":
    main()