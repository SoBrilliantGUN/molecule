"""add events_json to agent_runs

Revision ID: 0002_agent_runs_events
Revises: 0001_initial
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002_agent_runs_events'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('events_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_runs', 'events_json')