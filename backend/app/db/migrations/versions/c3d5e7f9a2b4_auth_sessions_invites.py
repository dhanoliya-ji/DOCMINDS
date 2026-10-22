"""Auth hardening: invites, server-side sessions, emailed tokens, API key roles

WHAT THIS MIGRATION ADDS
------------------------
Four new tables and a handful of columns, all for the account and access
control work (api/v1/auth.py and api/v1/org.py):

    invites          the only way to join an existing organization. Signup
                     used to accept any org_id with any role.
    refresh_tokens   every refresh token issued, hashed, so it can be
                     rotated on use and revoked on logout.
    revoked_tokens   the denylist of access tokens signed out before they
                     expired, keyed by the token's `jti`.
    user_tokens      single-use emailed tokens: password reset and email
                     verification.

    users.password_changed_at   access tokens issued before it are refused,
                                so a password reset ends every session.
    api_keys.role               what a key may do (viewer/editor/admin).
    api_keys.last_used_at       so unused keys can be spotted and revoked.
    api_keys.revoked_at         when a key was revoked.
    ix_audit_logs_org_created   serves the audit log screen's only query:
                                one organization's events, newest first.

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
Every new column is nullable or has a server default, so this runs on a live
database without touching existing rows' meaning:

    * existing API keys get role 'Employee' (viewer) -- the least privilege.
      A key that needs more must be re-created by an Admin, deliberately.
    * existing users get password_changed_at NULL, i.e. "never reset", so no
      current session is ended by the upgrade.

Refresh tokens issued BEFORE this migration have no row in refresh_tokens, so
their next /auth/refresh fails and the user signs in once more. That is the
one visible effect, and it is the price of making refresh tokens revocable.

Revision ID: c3d5e7f9a2b4
Revises: a1b2c3d4e5f6
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d5e7f9a2b4"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New columns on existing tables
    # ------------------------------------------------------------------
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # `server_default` fills every existing row with 'Employee' as the column
    # is added, which is what lets it be NOT NULL from the start.
    op.add_column(
        "api_keys",
        sa.Column("role", sa.String(length=50), server_default="Employee", nullable=False),
    )
    op.add_column(
        "api_keys", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_index(
        "ix_audit_logs_org_created", "audit_logs", ["org_id", "created_at"], unique=False
    )

    # ------------------------------------------------------------------
    # 2. invites
    # ------------------------------------------------------------------
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invites_org_id", "invites", ["org_id"], unique=False)
    op.create_index("ix_invites_email", "invites", ["email"], unique=False)
    op.create_index("ix_invites_token_hash", "invites", ["token_hash"], unique=True)

    # ------------------------------------------------------------------
    # 3. refresh_tokens
    # ------------------------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], unique=False
    )

    # ------------------------------------------------------------------
    # 4. revoked_tokens (the access-token denylist)
    # ------------------------------------------------------------------
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"], unique=False
    )

    # ------------------------------------------------------------------
    # 5. user_tokens (password reset + email verification)
    # ------------------------------------------------------------------
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_tokens_user_id", "user_tokens", ["user_id"], unique=False)
    op.create_index("ix_user_tokens_token_hash", "user_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    """The exact mirror of `upgrade`, in reverse order."""
    op.drop_index("ix_user_tokens_token_hash", table_name="user_tokens")
    op.drop_index("ix_user_tokens_user_id", table_name="user_tokens")
    op.drop_table("user_tokens")

    op.drop_index("ix_revoked_tokens_expires_at", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")

    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_invites_token_hash", table_name="invites")
    op.drop_index("ix_invites_email", table_name="invites")
    op.drop_index("ix_invites_org_id", table_name="invites")
    op.drop_table("invites")

    op.drop_index("ix_audit_logs_org_created", table_name="audit_logs")

    op.drop_column("api_keys", "revoked_at")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "role")

    op.drop_column("users", "password_changed_at")
