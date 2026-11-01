import pytest
from ai_service import normalize_hindi_number_words, parse_hinglish_input

def test_normalize_hindi_number_words():
    assert normalize_hindi_number_words("paanch sau") == "500"
