"""drop dead columns from agent_runs

Revision ID: 0004_drop_dead_columns
Revises: 0003_races_client_key
Create Date: 2026-11-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004_drop_dead_columns'
down_revision: Union[str, None] = '0003_races_client_key'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # intent：意图识别已删（新建/迭代只按有无 project_id 判定），列从未写入。
    # checkpoint_json：断点续跑已迁到 LangGraph 自己的 PostgreSQL checkpoint 表。
    # usage_json：用量实际写在 chat_messages.meta_json，此处从未写入。
    op.drop_column('agent_runs', 'intent')
    op.drop_column('agent_runs', 'checkpoint_json')
    op.drop_column('agent_runs', 'usage_json')


def downgrade() -> None:
    op.add_column('agent_runs', sa.Column('intent', sa.String(), nullable=True))
    op.add_column('agent_runs', sa.Column('checkpoint_json', sa.Text(), nullable=True))
    op.add_column('agent_runs', sa.Column('usage_json', sa.Text(), nullable=True))
