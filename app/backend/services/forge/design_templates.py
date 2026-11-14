"""生成应用的视觉设计库：通用铁律（CRAFT）+ 一批可复用的视觉方向（TEMPLATES）。

这是一套「反 AI 模板感」的视觉设计规范：把「配色 / 排版 / 反套路 / 动效 / 无障碍」沉淀成
可执行的硬规则，再配 6 个差异鲜明的视觉方向。目标：让 LLM 生成的单文件 HTML 摆脱
「AI 模板感」，有一个明确、具体的视觉身份，而不是一句笼统的「设计得好看一点」。

- ``CRAFT``：所有生成物都必须遵守的硬性规则（反 AI 味 + 配色 + 排版 + 动效 + 无障碍）。
- ``TEMPLATES``：6 个差异鲜明的视觉方向，每个给出一套具体 token（4–6 个命名 hex）、
  字体、布局概念与「唯一记忆点」。生成时按需求主题/用户指定风格挑 1–2 个注入 prompt，
  让模型三选一——既给它方向，又保留「为这个应用专门设计」的发挥空间。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 通用铁律：所有生成物无条件遵守。刻意写得「可检查」，不是空泛的审美口号。
# ---------------------------------------------------------------------------

CRAFT = """【视觉铁律 —— 生成的界面必须像「为这个应用专门设计的」，而非 AI 模板】

配色：
- 用一套 4–6 个命名 token（--bg / --surface / --fg / --muted / --border / --accent），
  按用途命名，不要用 --blue-500 这类色相名。中性色占 70–90%，一个主色 --accent 占 5–10%。
- 一屏内 --accent 的可见使用不超过 2 处（例如：一个标签/按钮 + 一个主 CTA），其余靠留白与层级。
- 禁止把 Tailwind 默认 indigo（#6366f1 / #4f46e5 / #4338ca / #8b5cf6 / #7c3aed）当主色。
- 禁止 hero 用「紫→蓝 / 蓝→青 / 靛→粉」这类双色信任渐变；一个扁平底色 + 有态度的标题更好。
- 正文对比度 ≥ 4.5:1；深色主题背景用 #0f0f0f 而非纯黑、前景用 #f0f0f0 而非纯白。

排版（中文界面尤其重要）：
- 最多 2 个字族，标题与正文各司其职；标题不要只写 font-family: system-ui。
- 字号用 1.2~1.25 的乘法阶梯，一屏最多 6–8 档；只用 3 个档位的字重（正文/强调/标题）。
- 中文大标题行高必须 ≥1.3（≥32px 时），正文行高 1.7~1.8；不要给中文设负字距（letter-spacing）。
- 全大写英文标签必须加 0.06em~0.1em 的字距；正文 letter-spacing 保持 0。
- 正文每行 ≤65ch；正文不要 justify。

反 AI 味：
- 图标用 1.6~1.8px 细描边 monoline SVG（currentColor），不要用 ✨🚀🎯⚡🔥💡 这类 emoji 当功能图标。
- 不要「圆角卡片 + 左侧彩色竖条」这种 AI 仪表盘卡片形状。
- 不要每段标题上都压一行全大写 eyebrow 小标签、不要「A · B · C」中点串词、不要「词 — 片段」破折号标签、
  不要在按钮/链接后统一加「→」。
- 不编造指标（如「10×更快」「99.9% 可用」）；空状态是行动邀请，不是「暂无数据」四个字。

动效与底线：
- 动效只用于确认状态变化，默认 150ms；非页面跳转的微交互 <500ms；transform 类动效必须尊重
  prefers-reduced-motion。别做每个卡片 hover 都弹一下、每段都淡入的「AI 通用动效」。
- 响应式到移动端、可见键盘焦点、语义化标签。

状态与表单（页面太简单的根因常是「只画了有数据的样子」）：
- 每个会取数/录入数据的界面都要有 5 个状态：加载中、空、错误、有数据、边界（超长文本/超大量/缺可选字段）。
  加载态配 15s「还在加载」兜底；空状态是行动邀请（一句话 + 一个主按钮），不是「暂无数据」四个字。
- 错误三连：发生了什么 → 为什么（若可知）→ 用户能做什么（重试/替代路径），并保留用户已输入的内容。
- 表单校验在**失焦时**触发（:user-invalid），不是首次击键；一旦报错就切到 input 事件实时重校验，改对即消。
  提交用 form.requestSubmit()（会走校验），不要 form.submit()（跳过校验）。
- 每个输入配 <label for>；错误用 aria-describedby 关联、aria-invalid 标记、role="alert" 播报。
- 交互元素键盘可达（不要 tabindex>0），删掉 focus 外框前必须给 :focus-visible 等价焦点样式。

布局骨架（结构就是信息，别把一切都堆成一张扁平列表）：
- 用语义标签搭骨架：<header>（应用名 + 一句副标题 + 主操作）→ <main>（主体）→ 可选的 <footer>。
- 主体通常三段：① 顶部「主操作区」（新增/搜索/统计摘要，最显眼、紧贴 header）；② 中间「内容区」
  （列表/表格/卡片/看板，占最多空间）；③ 每种内容都配对应的空状态与错误态。
- 整体骨架用 CSS Grid（单列，或「侧栏 + 主区」），卡片/行内用 Flexbox。
- 间距用「接近性」分层：同组元素 8–12px，组与组之间 32–48px；不要所有间距一个值。
- 密度贴合方向：数据/工具类紧凑（行高收窄、细分隔线），阅读/生活类宽松（多留白）。
- 每屏只突出一个主操作（视觉最强的按钮），其余操作降级成次要/文字按钮。

交互与信息架构（让人不用学就会用）：
- 单一决策屏只放 3–5 个主选项，其余折叠进「更多」；定价 3–4 档、产品网格首屏 6–9 个卡片。
- 同类元素必须同款式（所有行一样、所有次要按钮一样），唯一差异留给「要突出/被选中」的那一个。
- 相关字段用「分组标题 + 分隔/卡片容器」归拢，别把几十个开关平铺成一面墙。
- 触摸目标 ≥24×24px；高频操作放在顺手的位置（移动端拇指弧区）。
- 复用品类惯例（按钮位置、图标含义），创新只花在一个刀刃上，别处处标新。

图表（看板/报表类需要时才画，别硬塞）：
- 只用内联 SVG，不引任何 JS 图表库。折线 = <polyline> + 半透明 area 填充；柱状 = 一组 <rect>；
  坐标轴用 --muted 的小字弱化。
- 颜色全部来自 token：主色只给「最关键的那一条线 / 一个柱」，其余用 --muted 的层级拉开。
- 数字永远比图可靠：数据摘要（大数字 + 标签）放最显眼处，图次之。

核心心法：约 80% 用久经考验的结构，20% 花在一个大胆的选择上（一个字体决策 / 一个配色决策 /
一个出人意料的比例 / 一句有记忆点的文案）。让这个应用有「只有用过这个产品的人才会写出来的细节」。
"""

# ---------------------------------------------------------------------------
# 一批视觉方向：每个给一套具体 token + 字体 + 布局概念 + 唯一记忆点。
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "id": "swiss",
        "name": "极简瑞士",
        "keys": ["工具", "计算", "清单", "待办", "提醒", "转换", "查询", "设置", "记账"],
        "recipe": (
            "方向：极简瑞士（简洁克制）\n"
            "- token：--bg #ffffff；--surface #fafafa；--fg #111111；--muted #777777；--border #e2e2e2；--accent #111111\n"
            "- 字体：标题 Inter/-apple-system + PingFang SC；正文同族、中等字重；数字可用等宽小字\n"
            "- 布局骨架：单列 + 顶部极简 header（应用名 + 一句副标题）→ 主操作区（新增/搜索，紧贴 header）→ 内容区（列表，行间 hairline 分隔）→ 底部空状态提示；严格左对齐、几乎无圆角\n"
            "- 记忆点：一个占据视觉焦点的大号标题或一个超大的关键数字，其余保持安静\n"
            "- 克制：主色就是黑，靠大小、字重、留白分层，别加装饰"
        ),
    },
    {
        "id": "warm-editorial",
        "name": "温暖编辑",
        "keys": ["笔记", "阅读", "读书", "日记", "博客", "文章", "主页", "个人", "简历", "写作", "摘录"],
        "recipe": (
            "方向：温暖编辑（纸面杂志感）\n"
            "- token：--bg #fbf7f0；--surface #fffdf8；--fg #1f1a16；--muted #7d7168；--border #ded3c5；--accent #9a5a2f\n"
            "- 字体：标题 Georgia/serif（衬线）；正文 Inter/PingFang SC（无衬线），两族分明\n"
            "- 布局骨架：居中窄栏（max-width 65ch），衬线大标题在最上 → 正文/列表随之 → 底部一个低调的添加入口；标题与正文间留白充足、节奏像杂志\n"
            "- 记忆点：一个衬线大标题或首字下沉/引言块，让阅读成为主角"
        ),
    },
    {
        "id": "neobrutalism",
        "name": "粗野波普",
        "keys": ["游戏", "抽奖", "挑战", "成就", "番茄", "计时", "倒计时", "排行", "打卡", "奖励"],
        "recipe": (
            "方向：粗野波普（大胆、好玩、有态度）\n"
            "- token：--bg #fff4cf；--surface #fffaf0；--fg #2a1810；--muted #8a6652；--border #2a1810；--accent #d24b1f\n"
            "- 字体：标题 Arial Black/Impact/sans-serif 加粗；正文 Inter/PingFang SC\n"
            "- 布局骨架：顶部撞色 header（大标题 + 一个主按钮）→ 主体是描边卡片网格（2–3 列、每卡一个操作）；元素都带粗黑描边 + 硬阴影（box-shadow 4px 4px 0 描边色）\n"
            "- 记忆点：一个醒目的彩色主按钮或一张撞色卡片，点击有 2px 下沉的硬反馈\n"
            "- 克制：装饰大胆但信息仍清楚，别每处都加描边"
        ),
    },
    {
        "id": "dashboard",
        "name": "数据面板",
        "keys": ["看板", "表格", "仪表盘", "客户", "项目", "跟进", "招聘", "财务", "报表", "数据", "管理", "库存", "订单", "CRM"],
        "recipe": (
            "方向：数据面板（信息密度高、可扫读）\n"
            "- token：--bg #f4f7fb；--surface #ffffff；--fg #111827；--muted #64748b；--border #d8e2ee；--accent #0ea5e9\n"
            "- 字体：Inter/PingFang SC；数字与日期用等宽字体小标签\n"
            "- 布局骨架：顶部 header（标题 + 一个统计摘要大数字）→ 一个内联 SVG 主图表 → 下方数据表格/列表（清晰列头 + 细分隔线）；紧凑可扫读，状态用色块/徽标\n"
            "- 记忆点：顶部一个聚焦的统计数字或一条进度/状态摘要，一屏看懂整体"
        ),
    },
    {
        "id": "mono",
        "name": "极客终端",
        "keys": ["代码", "命令行", "终端", "日志", "调试", "脚本", "API", "开发", "密钥", "JSON", "命令"],
        "recipe": (
            "方向：极客终端（等宽、像工具本身）\n"
            "- token：--bg #ffffff；--surface #f7f7f7；--fg #111111；--muted #707070；--border #d9d9d9；--accent #111111\n"
            "- 字体：标题与正文都用 IBM Plex Mono/ui-monospace/monospace（单字族多字重）\n"
            "- 布局骨架：顶部终端风 header（前导提示符「>」+ 标题）→ 主体是等宽堆叠的输入区 + 列表（每行一条记录）→ 底部状态行；细线框 + 单色，像命令行\n"
            "- 记忆点：一个终端提示符或 tab 标签作为视觉锚点，界面本身就像它要生成的工具"
        ),
    },
    {
        "id": "clay",
        "name": "粘土柔和",
        "keys": ["习惯", "健身", "健康", "喝水", "睡眠", "生活", "护肤", "冥想", "宠物", "饮食"],
        "recipe": (
            "方向：粘土柔和（圆润、软 3D、治愈）\n"
            "- token：--bg #f7eee6；--surface #fff8f1；--fg #2b211c；--muted #8a7a70；--border #dac8b9；--accent #b46a46\n"
            "- 字体：Inter/PingFang SC，字重偏柔，标题不用太锐利\n"
            "- 布局骨架：居中大圆角卡片装主内容 → 卡内柔和的表单 + 列表 → 底部一个大的圆角主按钮；内阴影 + 外阴影营造「凸起的软泥」质感\n"
            "- 记忆点：一个大的圆角主按钮或一张凸起卡片，按压有轻微下沉"
        ),
    },
]

# 前端「更多选项 → 视觉风格」的中文选项 → 模板 id。
THEME_MAP = {
    "简洁克制": "swiss",
    "活泼明快": "neobrutalism",
    "商务正式": "dashboard",
    "极客冷峻": "mono",
    "温暖": "warm-editorial",
    "柔和": "clay",
}

# 前端四个风格名语义唯一，可直接子串命中（normalize 可能把「风格：商务正式」改写成「商务正式风格」）。
_DIRECT_THEMES = {
    "简洁克制": "swiss",
    "活泼明快": "neobrutalism",
    "商务正式": "dashboard",
    "极客冷峻": "mono",
}

_ID_BY_INDEX = {t["id"]: t for t in TEMPLATES}


def _explicit_theme(requirement: str) -> str | None:
    """从「风格：X」或直接出现的风格名里取模板 id，取不到返回 None。"""
    req = requirement or ""
    m = re.search(r"(?:视觉)?风格[：:]\s*([^\s，。；、\n]+)", req)
    if m:
        tid = THEME_MAP.get(m.group(1).strip())
        if tid:
            return tid
    for name, tid in _DIRECT_THEMES.items():
        if name in req:
            return tid
    return None


def _score(requirement: str) -> list[tuple[int, dict]]:
    """按需求命中关键词数给模板打分，返回 (命中数, 模板) 降序。"""
    req = requirement or ""
    scored = []
    for t in TEMPLATES:
        hits = sum(1 for k in t["keys"] if k in req)
        if hits:
            scored.append((hits, t))
    scored.sort(key=lambda x: -x[0])
    return scored


def pick_templates(requirement: str) -> list[dict]:
    """为一条需求挑 1–3 个候选视觉方向（模型在 prompt 里三选一）。

    优先级：显式「风格：X」> 按主题关键词命中 > 默认（极简瑞士 + 温暖编辑）。
    """
    explicit = _explicit_theme(requirement)
    if explicit:
        chosen = _ID_BY_INDEX[explicit]
        # 再补一个与其差异最大的方向，给模型一个对照。
        other = next((t for t in TEMPLATES if t["id"] != explicit), None)
        return [chosen, other] if other else [chosen]

    scored = _score(requirement)
    if scored:
        return [t for _, t in scored[:2]]
    return [_ID_BY_INDEX["swiss"], _ID_BY_INDEX["warm-editorial"]]


def style_block(requirement: str) -> str:
    """返回要注入生成 prompt 的视觉设计块：铁律 + 候选方向。"""
    candidates = pick_templates(requirement)
    recipes = "\n\n".join(f"候选 {i+1} · {t['name']}\n{t['recipe']}" for i, t in enumerate(candidates))
    return (
        CRAFT
        + "\n\n【本应用的视觉方向：从下面候选中选最贴合需求主题与受众的那一个，完整地贯彻它的配色/字体/布局，"
        "不要混搭；若都不贴，也可在其精神上自由发挥但必须遵守上面的铁律】\n"
        + recipes
    )
