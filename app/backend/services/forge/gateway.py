"""LLM gateway: the ONLY exit for model calls.

Pipeline: precheck (breaker + budget) -> snapshot reuse -> retry/timeout -> pricing -> persist snapshot/cost.
Never call AIHubService directly from agent code.
"""

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from openai import AsyncOpenAI

from models.llm_calls import LlmCalls
from services.forge import repo
from services.forge.breaker import breakers
from services.forge.budget import Budget, BudgetExceeded
from services.forge.util import DomainError, sha

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("LLM_MAIN_MODEL", "deepseek-ai/DeepSeek-V4-Flash")

# 主生成走 DeepSeek 官方 API（更快更稳）；核验（跨家换脑）才走 SiliconFlow。
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
# DeepSeek 挂了之后的 fallback 模型（SiliconFlow 侧的 DeepSeek 等价物）。
DEEPSEEK_FALLBACK_MODEL = os.environ.get("DEEPSEEK_FALLBACK_MODEL", "deepseek-ai/DeepSeek-V4-Flash")

# 按 provider 分熔断器：DeepSeek 的失败不能把 SiliconFlow 的 fallback 一起熔断。
BREAKER_KEYS = {
    "deepseek": ["llm:deepseek"],
    "siliconflow": ["llm:siliconflow"],
}


class GatewayError(DomainError):
    pass


_siliconflow_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Lazily build the SiliconFlow OpenAI-compatible client from env config."""
    global _siliconflow_client
    if _siliconflow_client is None:
        api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        base_url = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        if not api_key:
            raise GatewayError("未配置 SILICONFLOW_API_KEY")
        # 生成单文件 HTML 可能要几分钟。SDK 默认 600s 总超时会掐掉慢请求，
        # 这里抬到 30 分钟给足余量；真正的超时约束由调用方 asyncio.wait_for 控制。
        _siliconflow_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=1800.0,
            max_retries=0,  # 网关自带重试，交给网关
        )
    return _siliconflow_client


_deepseek_client: AsyncOpenAI | None = None


def _get_deepseek_client() -> AsyncOpenAI:
    """Lazily build the DeepSeek 官方 API client (主生成用，更快更稳)."""
    global _deepseek_client
    if _deepseek_client is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        if not api_key:
            raise GatewayError("未配置 DEEPSEEK_API_KEY")
        _deepseek_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=1800.0,
            max_retries=0,  # 网关自带重试，交给网关
        )
    return _deepseek_client


def is_retryable(exc: BaseException) -> bool:
    """Only transient transport errors are retried; business errors are permanent."""
    if isinstance(exc, (DomainError, BudgetExceeded, ValueError)):
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    text = str(exc).lower()
    return any(tag in text for tag in ("timeout", "timed out", "connection", "429", "502", "503", "504", "rate limit", "overloaded"))


@dataclass
class RetryPolicy:
    attempts: int = 3
    mode: str = "jittered"  # fixed | exponential | jittered
    base: float = 0.8

    def delay(self, attempt: int) -> float:
        if self.mode == "fixed":
            return self.base
        backoff = self.base * (2 ** attempt)
        return backoff * (0.5 + random.random()) if self.mode == "jittered" else backoff


class PricingStrategy:
    """Pricing mode lives in code (one class per vendor); numeric prices are data (CNY per 1M tokens)."""

    def __init__(self, prompt_per_m: float, completion_per_m: float):
        self.prompt_per_m, self.completion_per_m = prompt_per_m, completion_per_m

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.prompt_per_m + completion_tokens * self.completion_per_m) / 1_000_000


PRICING: dict[str, PricingStrategy] = {
    "deepseek-v4-flash": PricingStrategy(0.5, 2),
    "deepseek-ai/DeepSeek-V4-Flash": PricingStrategy(0.5, 2),
    "deepseek-ai/DeepSeek-V4-Pro": PricingStrategy(2, 8),
    "Qwen/Qwen3-Coder-480B-A35B-Instruct": PricingStrategy(2, 8),
    "Qwen/Qwen3-235B-A22B-Instruct": PricingStrategy(1, 3),
    "zai-org/GLM-5.3": PricingStrategy(1, 4),
}
FALLBACK_PRICING = PricingStrategy(10, 40)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


@dataclass
class LLMResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_cny: float
    latency_ms: int
    cached: bool = False


@dataclass
class RunContext:
    """Per-run state: run_key scopes snapshots; budget is shared by every call in the run."""

    run_key: str
    project_id: int | None = None
    budget: Budget = field(default_factory=Budget)
    call_seq: dict = field(default_factory=dict)


async def _load_snapshot(run_key: str, call_key: str) -> dict | None:
    async with repo.session() as s:
        row = (
            await s.execute(select(LlmCalls).where(LlmCalls.run_key == run_key, LlmCalls.call_key == call_key, LlmCalls.status == "ok"))
        ).scalars().first()
        return repo.row_to_dict(row) if row else None


async def _record(ctx: RunContext, purpose: str, model: str, call_key: str, status: str, pt=0, ct=0, cost=0.0, latency=0, error=None, output=None) -> None:
    try:
        await repo.add(
            LlmCalls, run_key=ctx.run_key, project_id=ctx.project_id, purpose=purpose, model=model, call_key=call_key,
            prompt_tokens=pt, completion_tokens=ct, cost_cny=cost, latency_ms=latency, status=status, error=error, output=output,
        )
    except Exception as exc:  # observability must never break the call
        logger.warning("llm call record failed: %s", exc)


async def chat(
    ctx: RunContext,
    messages: list[dict],
    *,
    purpose: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout: float = 600.0,  # DeepSeek 长生成可能数分钟，默认给足；生成/规划调用常显式覆盖
    policy: RetryPolicy | None = None,
    provider: str = "deepseek",  # "deepseek"（主生成）| "siliconflow"（跨家核验）
    thinking: bool | None = None,  # None=默认；False=关闭思考（推理型模型关闭后快很多）
) -> LLMResult:
    client = _get_deepseek_client() if provider != "siliconflow" else _get_client()
    model = model or (DEEPSEEK_MODEL if provider != "siliconflow" else DEFAULT_MODEL)
    breaker_keys = BREAKER_KEYS.get(provider, BREAKER_KEYS["deepseek"])
    policy = policy or RetryPolicy()
    ctx.budget.check()

    hint = ctx.budget.soft_hint()
    msgs = list(messages) + ([{"role": "system", "content": hint}] if hint else [])

    # Content-hash key + occurrence index: identical calls within one run get distinct, replayable keys.
    base_key = sha(model, msgs, temperature, max_tokens)
    idx = ctx.call_seq.get(base_key, 0)
    ctx.call_seq[base_key] = idx + 1
    call_key = f"{base_key}:{idx}"

    snap = await _load_snapshot(ctx.run_key, call_key)
    if snap and snap.get("output") is not None:
        return LLMResult(snap["output"], model, snap.get("prompt_tokens") or 0, snap.get("completion_tokens") or 0, 0.0, 0, cached=True)

    allowed, tripped = await breakers.allow(breaker_keys)
    if not allowed:
        raise GatewayError(f"模型调用已熔断（{tripped}），请稍后重试")

    ctx.budget.add_turn()
    last_exc: BaseException | None = None
    for attempt in range(policy.attempts):
        started = time.time()
        try:
            kwargs = {
                "model": model,
                "messages": [{"role": m["role"], "content": m["content"]} for m in msgs],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            if thinking is False:
                # 关闭思考模式：DeepSeek 官方用 thinking.disabled，硅基流动用顶层 enable_thinking=false。
                kwargs["extra_body"] = (
                    {"enable_thinking": False} if provider == "siliconflow" else {"thinking": {"type": "disabled"}}
                )
            response = await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=timeout,
            )
            latency = int((time.time() - started) * 1000)
            content = (response.choices[0].message.content if response.choices else "") or ""
            usage = response.usage
            prompt_tokens = (usage.prompt_tokens if usage else 0) or sum(estimate_tokens(str(m["content"])) for m in msgs)
            completion_tokens = (usage.completion_tokens if usage else 0) or estimate_tokens(content)
            cost = PRICING.get(model, FALLBACK_PRICING).cost(prompt_tokens, completion_tokens)
            ctx.budget.charge(prompt_tokens + completion_tokens, cost)  # cost computed once, here
            await breakers.record(breaker_keys, True)
            await _record(ctx, purpose, model, call_key, "ok", prompt_tokens, completion_tokens, cost, latency, output=content)
            return LLMResult(content, model, prompt_tokens, completion_tokens, cost, latency)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            retryable = is_retryable(exc)
            logger.warning("llm call failed (attempt %d, retryable=%s): %s", attempt + 1, retryable, exc)
            if not retryable or attempt == policy.attempts - 1:
                break
            await asyncio.sleep(policy.delay(attempt))
    await breakers.record(breaker_keys, False)
    await _record(ctx, purpose, model, call_key, "error", error=str(last_exc)[:500])
    if provider == "deepseek":
        # DeepSeek 挂了（有限重试后仍失败）→ fallback 到 SiliconFlow 用对应模型再试一次
        logger.warning("deepseek provider failed（%s），fallback 到 siliconflow model=%s", last_exc, DEEPSEEK_FALLBACK_MODEL)
        return await chat(
            ctx, messages,
            purpose=purpose, model=DEEPSEEK_FALLBACK_MODEL,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
            policy=policy, provider="siliconflow",
        )
    raise GatewayError(f"模型调用失败：{last_exc}") from last_exc


async def usage_summary() -> dict:
    from sqlalchemy import func

    async with repo.session() as s:
        total = (await s.execute(select(func.count(), func.coalesce(func.sum(LlmCalls.prompt_tokens + LlmCalls.completion_tokens), 0), func.coalesce(func.sum(LlmCalls.cost_cny), 0.0)).where(LlmCalls.status == "ok"))).one()
        by_model = (await s.execute(select(LlmCalls.model, func.count(), func.coalesce(func.sum(LlmCalls.cost_cny), 0.0)).where(LlmCalls.status == "ok").group_by(LlmCalls.model))).all()
        errors = (await s.execute(select(func.count()).select_from(LlmCalls).where(LlmCalls.status == "error"))).scalar_one()
    return {
        "calls": total[0], "tokens": int(total[1]), "cost_cny": round(float(total[2]), 4), "errors": errors,
        "by_model": [{"model": m, "calls": c, "cost_cny": round(float(cost), 4)} for m, c, cost in by_model],
    }
