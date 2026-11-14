"""共享落库：把生成结果写进 project + 幂等落聊天记录（单路与赛马共用）。

单路 ``agent._persist_project`` 与赛马 ``extras.select_race`` 之前各自写一遍「user/assistant
聊天记录 + 用量/核验 meta」，逻辑分叉后修一处漏一处。这里收敛成唯一入口：
- sanitize HTML 后写入（新建）或覆盖（迭代）project；
- 幂等：若该 project 已存在同 prompt 的 user 消息（崩溃续跑会再次进入），跳过聊天记录，
  避免产生重复的 user/assistant。
"""

from models.chat_messages import ChatMessages
from services.forge import codegen, ops, repo
from services.forge.util import dumps


async def persist_result(
    *,
    project_id: int | None,
    html: str,
    prompt: str,
    title: str = "",
    description: str = "",
    verify: dict | None = None,
    verify_failed: bool = False,
    usage: dict | None = None,
) -> dict:
    """Persist generated HTML into a project (+ idempotent chat messages). Returns the project dict."""
    html = codegen.sanitize_html(html)
    title = title or prompt[:40]
    if project_id is not None:
        project = await ops.update_project_html(project_id, html, title)
    else:
        project = await ops.create_project(title, html, description=description or prompt[:200])

    # 幂等：崩溃续跑会再次进入此处，若项目已存在同 prompt 的用户消息（上次已落过）则跳过，
    # 避免续跑产生重复的 user/assistant 聊天记录。
    dup = await repo.find(
        ChatMessages,
        ChatMessages.project_id == project["id"],
        ChatMessages.role == "user",
        ChatMessages.content == prompt,
        limit=1,
    )
    if not dup:
        await repo.add(ChatMessages, project_id=project["id"], role="user", content=prompt)
        meta = {"usage": usage, "verify": verify or {}, "verify_failed": bool(verify_failed)}
        await repo.add(
            ChatMessages,
            project_id=project["id"],
            role="assistant",
            content=f"已生成「{project['name']}」。",
            meta_json=dumps(meta),
        )
    return project
