"""Add explicit incident severity, source, and resolution time."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2107b6ad348"
down_revision: Union[str, None] = "e94a1c72d601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(
            sa.Column("severity", sa.String(length=16), server_default="INFO", nullable=False)
        )
        batch_op.add_column(sa.Column("source", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("source")
        batch_op.drop_column("severity")
