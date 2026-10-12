from scamshield.models import RawMessage
from scamshield.normalize import extract_phones, extract_urls, normalize, normalize_sender, phone_variants, to_e164
from scamshield.redact import redact


def test_phone_formats_normalise_to_e164():
    assert to_e164("212-555-0142") == "+12125550142"
    assert to_e164("1-800-555-0166") == "+18005550166"
    assert to_e164("090-0000-0145") == "+819000000145"
    assert to_e164("+81 90 0000 0145") == "+819000000145"
    assert to_e164("12345") is None


def test_extracts_urls_and_phones_without_confusing_them():
    text = "Pay $1,284.50 at http://evil.example.com/a-1234-5678-9012. Call (212) 555-0155 or +1 800 555 0166!"
    assert extract_urls(text) == ["http://evil.example.com/a-1234-5678-9012"]
    assert extract_phones(text) == ["+12125550155", "+18005550166"]


def test_sender_normalisation():
    assert normalize_sender("Kenji <Kenji.Hayashi@Family.example.com>") == "kenji.hayashi@family.example.com"
    assert normalize_sender("212-555-0101") == "+12125550101"
    assert normalize_sender(None) is None


def test_normalize_builds_message():
    m = normalize(RawMessage(body=" hi http://a.test/x call 212-555-0100 ", sender="212-555-0101", channel="sms"))
    assert (m.channel, m.sender, m.urls, m.phones) == ("sms", "+12125550101", ["http://a.test/x"], ["+12125550100"])


def test_phone_variants():
    assert phone_variants("+12125550142") == ["+12125550142", "212-555-0142", "(212) 555-0142"]


def test_redacts_cards_my_number_ssn_and_passwords():
    r = redact("Card 4111 1111 1111 1111, My Number 1234 5678 9012, SSN 123-45-6789, password: hunter2, パスワードは abc123")
    assert "4111" not in r.text and "9012" not in r.text and "6789" not in r.text
    assert "hunter2" not in r.text and "abc123" not in r.text
    assert r.counts == {"card_number": 1, "ssn": 1, "my_number": 1, "password": 2}


def test_redaction_leaves_phones_and_ordinary_numbers():
    text = "Call +81 90 0000 0145 or 212-555-0155 about order 10482 for $1,284.50. We never ask for your password by phone."
    assert redact(text).text == text
