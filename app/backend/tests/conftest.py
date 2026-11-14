"""让 pytest 能以 `services.forge.xxx` 导入后端模块（把 backend 目录加入 sys.path）。

这些测试只覆盖**确定性纯函数**（不碰 DB / 不调 LLM）：核心库导入是惰性的
（core.database 引擎懒加载、LLM client 懒构建），所以 import 不会真的连数据库。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
