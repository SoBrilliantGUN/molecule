"""add client_key to races

Revision ID: 0003_races_client_key
Revises: 0002_agent_runs_events
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003_races_client_key'
down_revision: Union[str, None] = '0002_agent_runs_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('races', sa.Column('client_key', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('races', 'client_key')
