"""persist_result 幂等落库测试（mock 掉 repo/ops，不碰真实 DB）。

覆盖两条路径：
- 新建：create_project + 追加 user/assistant 聊天记录（meta 带 verify_failed）。
- 续跑：同 prompt 的 user 消息已存在 → 跳过聊天记录，不产生重复 user/assistant。
"""

import asyncio
import json

import pytest

from services.forge import persist


class FakeOps:
    def __init__(self):
        self.created: list = []
        self.updated: list = []

    async def create_project(self, name, html, description=""):
        self.created.append((name, html, description))
        return {"id": 1, "name": name, "html": html}

    async def update_project_html(self, project_id, html, name=""):
        self.updated.append((project_id, html, name))
        return {"id": project_id, "name": name or "旧名", "html": html}


class FakeRepo:
    def __init__(self, dup_user: bool):
        self.dup_user = dup_user
        self.added: list = []

    async def find(self, model, *where, **kwargs):
        # 仅用于「查同 prompt 的 user 消息是否已存在」；返回非空表示已存在。
        return [{"id": 99}] if self.dup_user else []

    async def add(self, model, **fields):
        self.added.append((model.__name__, fields))
        return {"id": len(self.added)}


def _run(coro):
    return asyncio.run(coro)


def test_persist_new_project_adds_chat(monkeypatch):
    ops = FakeOps()
    repo = FakeRepo(dup_user=False)
    monkeypatch.setattr(persist, "ops", ops)
    monkeypatch.setattr(persist, "repo", repo)

    project = _run(
        persist.persist_result(
            project_id=None,
            html="<!doctype html><html><head><title>待办</title></head><body></body></html>",
            prompt="做一个待办应用",
            title="待办",
            verify={"score": 1.0},
            verify_failed=False,
            usage={"tokens": 10},
        )
    )

    assert project["id"] == 1
    assert len(ops.created) == 1 and ops.updated == []
    roles = [f["role"] for _, f in repo.added]
    assert roles == ["user", "assistant"]
    meta = json.loads(repo.added[1][1]["meta_json"])
    assert meta["verify_failed"] is False
    assert meta["usage"]["tokens"] == 10


def test_persist_resume_skips_chat_when_dup(monkeypatch):
    """崩溃续跑再次落库：同 prompt 的 user 消息已存在 → 不再追加聊天记录。"""
    ops = FakeOps()
    repo = FakeRepo(dup_user=True)
    monkeypatch.setattr(persist, "ops", ops)
    monkeypatch.setattr(persist, "repo", repo)

    project = _run(
        persist.persist_result(
            project_id=7,
            html="<html></html>",
            prompt="做一个待办应用",
            title="待办",
            verify={"score": 0.6},
            verify_failed=True,
        )
    )

    assert project["id"] == 7
    # 迭代走 update 而非 create
    assert ops.updated == [(7, "<html></html>", "待办")] and ops.created == []
    # 幂等：不重复落 user/assistant
    assert repo.added == []
