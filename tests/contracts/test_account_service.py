import pytest

from app import account_service


def test_get_or_init_account_returns_balance():
    # When backend not configured, should return initial from config without raising
    acc = account_service.get_or_init_account("USD")
    assert isinstance(acc.get("balance"), (int, float))
