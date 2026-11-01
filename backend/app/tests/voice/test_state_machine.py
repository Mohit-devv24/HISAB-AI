from voice.confirmation_service import is_confirmation_utterance

def test_is_confirmation_utterance():
    assert is_confirmation_utterance("yes save kar do") is True
    assert is_confirmation_utterance("save kardo") is True
    assert is_confirmation_utterance("haan") is True
    assert is_confirmation_utterance("nahi mat karo") is False
