import pytest

from shop.users import EmailAlreadyRegistered, UserRegistry


def test_email_uniqueness_is_case_insensitive():
    registry = UserRegistry()
    assert registry.register("ana@example.test") == "ana@example.test"
    with pytest.raises(EmailAlreadyRegistered):
        registry.register("ANA@example.test")
