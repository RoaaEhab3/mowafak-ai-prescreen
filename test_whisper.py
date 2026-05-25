from whisper_stt import WhisperSTT

stt = WhisperSTT("base")

result = stt.transcribe_audio("sample-speech-1m.wav")

print(result)