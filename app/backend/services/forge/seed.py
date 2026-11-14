"""预生成示例缓存：无 API Key 也能跑通「一句话 → 应用」的演示兜底。

每个示例是一份完整、自包含的单文件 HTML（内联 CSS/JS + localStorage），存成独立文件放在
``seed_examples/`` 下（避免内联成超大 Python 字面量）。它们**不**预置到「我的项目」——
只有当用户用对应提示词生成时，`match_mock()` 命中并直接返回缓存 HTML，做到「相同提示词
→ 相同应用」，且不消耗模型额度。
"""

from pathlib import Path

_EXAMPLES_DIR = Path(__file__).parent / "seed_examples"


def _load(name: str) -> str:
    """从 seed_examples/ 读取一份示例 HTML。读取失败直接抛，启动期越早暴露越好。"""
    return (_EXAMPLES_DIR / f"{name}.html").read_text(encoding="utf-8")


_READING_NOTES = _load("reading_notes")
_FITNESS_LOG = _load("fitness_log")
_RECRUITMENT = _load("recruitment")
_KANBAN = _load("kanban")

EXAMPLES = [
    {"name": "读书笔记", "prompt": "做一个读书笔记管理，记录书名、作者、阅读状态和评分", "html": _READING_NOTES},
    {"name": "健身打卡", "prompt": "做一个健身打卡应用，记录日期、动作、组数、重量", "html": _FITNESS_LOG},
    {"name": "招聘跟进", "prompt": "做一个招聘跟进表，包含候选人、岗位、面试阶段", "html": _RECRUITMENT},
    {"name": "项目看板", "prompt": "做一个带优先级、截止日、拖拽排序的项目看板", "html": _KANBAN, "keys": ["待办看板", "看板"]},
]


def match_mock(prompt: str) -> dict | None:
    """命中预生成示例则返回 {name, html}，否则 None。

    按示例名或其别名（keys）在提示词中出现做宽松匹配；「项目看板 / 待办看板 / 看板」都能
    命中看板示例，覆盖避坑指南首推的演示选题，无 API Key 也能走通闭环。
    """
    p = prompt or ""
    for ex in EXAMPLES:
        if ex["name"] in p or any(k in p for k in ex.get("keys", [])):
            return {"name": ex["name"], "html": ex["html"]}
    return None
