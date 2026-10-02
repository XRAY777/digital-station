"""Add searchable actor and state fields to audit records."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e94a1c72d601"
down_revision: Union[str, None] = "5c1bfb9e35c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("username", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("role", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("ip_address", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("state_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("old_value", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("new_value", sa.JSON(), nullable=True))
        batch_op.create_index("ix_audit_logs_session_id", ["session_id"])


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index("ix_audit_logs_session_id")
        batch_op.drop_column("new_value")
        batch_op.drop_column("old_value")
        batch_op.drop_column("state_version")
        batch_op.drop_column("ip_address")
        batch_op.drop_column("role")
        batch_op.drop_column("username")
        batch_op.drop_column("session_id")
