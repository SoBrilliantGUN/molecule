from services.forge.gateway import (
    PricingStrategy,
    RetryPolicy,
    estimate_tokens,
    is_retryable,
)
from services.forge.util import DomainError


def test_estimate_tokens_empty_min_one():
    assert estimate_tokens("") == 1


def test_estimate_tokens_cjk_counts_per_char():
    assert estimate_tokens("你好") == 2


def test_estimate_tokens_mixed():
    # 2 个 CJK 字 + 5 个 ASCII → 2 + 5//4 = 3
    assert estimate_tokens("你好world") == 3


def test_retry_policy_fixed():
    assert RetryPolicy(mode="fixed", base=0.5).delay(0) == 0.5


def test_retry_policy_exponential():
    assert RetryPolicy(mode="exponential", base=0.8).delay(1) == 1.6


def test_retry_policy_jittered_in_range():
    d = RetryPolicy(mode="jittered", base=0.8).delay(1)
    assert 0.8 <= d <= 1.6


def test_is_retryable_transient():
    assert is_retryable(TimeoutError()) is True
    assert is_retryable(Exception("429 rate limit")) is True


def test_is_retryable_permanent():
    assert is_retryable(DomainError("x")) is False
    assert is_retryable(ValueError("x")) is False


def test_pricing_cost():
    # 每 1M prompt 0.5、completion 2 → 各 1M 共 2.5
    assert PricingStrategy(0.5, 2).cost(1_000_000, 1_000_000) == 2.5
