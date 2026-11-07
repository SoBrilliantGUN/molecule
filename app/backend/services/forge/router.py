"""复杂度路由：每次用户消息先判长短，决定走 DAG 长任务还是 ReAct 短任务。

判断依据（按概率权重的启发式，而非只靠模型）：有无 project_id（新建/大改 → long；
迭代小改 → 倾向 short）+ 分类模型对消息复杂度的判定。分类模型输出 JSON，解析失败
fail-open 到 long（长任务走 DAG 分阶段，最坏也只是慢，不会因误判为 short 而一次超时）。
"""

import logging
import os

from services.forge import gateway
from services.forge.gateway import RunContext
from services.forge.util import extract_json, loads

logger = logging.getLogger(__name__)

# 复杂度分类模型（轻量）：复用意图模型，避免多一个模型配置项。
CLASSIFY_MODEL = os.environ.get("LLM_CLASSIFY_MODEL") or os.environ.get("LLM_INTENT_MODEL", "deepseek-ai/DeepSeek-V4-Flash")

CLASSIFY_SYSTEM = (
    "你是任务复杂度分类器。判断这条需求是「长任务」还是「短任务」。\n"
    "长任务（long）：新建一个完整应用，或对现有应用做大范围重写/新增多个功能，"
    "需要分阶段生成（结构、样式、交互等）。\n"
    "短任务（short）：对已有应用做局部小改动（改一个颜色/文案/字段、修一个小问题），"
    "或一段简短的问答/说明。\n"
    "只输出 JSON：{\"route\": \"long\"|\"short\", \"reason\": \"一句话原因\"}。\n"
    "当有聊天历史时，参考历史判断是否是对已生成应用的迭代。"
)


async def classify(ctx: RunContext, message: str, has_project: bool, history: str = "") -> str:
    """返回 \"long\"（DAG 分阶段）或 \"short\"（ReAct 单次生成）。

    has_project：是否已有 project（迭代修改）。强启发：无项目 → 大概率新建 → long。
    """
    if not has_project:
        # 新建应用，即便一句话也要完整生成，直接走长任务拆阶段
        return "long"

    # 有 project：让模型判断是局部小改还是大改/重写
    user = f"需求：{message}"
    if history:
        user += f"\n\n聊天历史（最近）：\n{history[:1500]}"
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": user},
            ],
            purpose="classify",
            model=CLASSIFY_MODEL,
            max_tokens=64,
            temperature=0,
        )
        data = extract_json(result.content)
        route = str(data.get("route", "")).strip().lower()
        if route in ("long", "short"):
            return route
        logger.warning("classify 返回未知 route %r，fail-open 到 long", route)
        return "long"
    except Exception as exc:  # noqa: BLE001 - 分类失败不阻断，fail-open 到 long
        logger.warning("classify 失败（%s），fail-open 到 long", exc)
        return "long"