"""initial forge schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agent_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_key', sa.String(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('intent', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('user_input', sa.Text(), nullable=True),
        sa.Column('checkpoint_json', sa.Text(), nullable=True),
        sa.Column('usage_json', sa.Text(), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_agent_runs_project_id'), 'agent_runs', ['project_id'], unique=False)

    op.create_table('breaker_states',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('breaker_key', sa.String(), nullable=False),
        sa.Column('state', sa.String(), nullable=True),
        sa.Column('failures', sa.Integer(), nullable=True),
        sa.Column('opened_at', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('breaker_key'))

    op.create_table('chat_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('meta_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_chat_messages_project_id'), 'chat_messages', ['project_id'], unique=False)

    op.create_table('llm_calls',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_key', sa.String(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('purpose', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('call_key', sa.String(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('cost_cny', sa.Float(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('output', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_llm_calls_project_id'), 'llm_calls', ['project_id'], unique=False)

    op.create_table('projects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('slug', sa.String(), nullable=True),
        sa.Column('html', sa.Text(), nullable=True),
        sa.Column('published', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'))

    op.create_table('races',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('results_json', sa.Text(), nullable=True),
        sa.Column('selected_index', sa.Integer(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_races_project_id'), 'races', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_races_project_id'), table_name='races')
    op.drop_table('races')
    op.drop_table('projects')
    op.drop_index(op.f('ix_llm_calls_project_id'), table_name='llm_calls')
    op.drop_table('llm_calls')
    op.drop_index(op.f('ix_chat_messages_project_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_table('breaker_states')
    op.drop_index(op.f('ix_agent_runs_project_id'), table_name='agent_runs')
    op.drop_table('agent_runs')
