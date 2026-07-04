"""chat meeting room

Revision ID: 20260704000000
Revises: 20260702200000
Create Date: 2026-07-04 00:00:00

Adds meeting-room fields to chat_messages:
  - agent_id   VARCHAR  nullable  (e.g. "strategist", "copywriter")
  - meeting_id VARCHAR  nullable  (groups all turns from one user message)
  - turn_index INTEGER  nullable  (0-based position within a meeting)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260704000000"
down_revision: Union[str, None] = "20260702200000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("agent_id", sa.String(), nullable=True))
    op.add_column("chat_messages", sa.Column("meeting_id", sa.String(), nullable=True))
    op.add_column("chat_messages", sa.Column("turn_index", sa.Integer(), nullable=True))
    op.create_index("ix_chat_messages_meeting_id", "chat_messages", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_meeting_id", table_name="chat_messages")
    op.drop_column("chat_messages", "turn_index")
    op.drop_column("chat_messages", "meeting_id")
    op.drop_column("chat_messages", "agent_id")
