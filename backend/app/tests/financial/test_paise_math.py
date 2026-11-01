def test_paise_integer_arithmetic():
    paise = 10050  # 100.50 INR
    assert paise / 100 == 100.5
    assert paise % 100 == 50
