"""core schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-09 13:30:39.489436
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

APP_TABLES = [
    "users", "user_settings", "telegram_links", "link_codes", "inbound_updates",
    "messages", "jobs", "outbound_messages", "audit_events",
]


def upgrade() -> None:
    op.create_table('inbound_updates',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('update_id', sa.BigInteger(), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Text(), server_default='received', nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('update_id')
    )
    op.create_index(op.f('ix_inbound_updates_telegram_user_id'), 'inbound_updates', ['telegram_user_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('audit_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('entity_type', sa.Text(), nullable=False),
    sa.Column('entity_id', sa.Text(), nullable=True),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('max_attempts', sa.Integer(), server_default='5', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('dedupe_key', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key')
    )
    op.create_index('ix_jobs_due', 'jobs', ['status', 'run_at'], unique=False)
    op.create_table('link_codes',
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), server_default='text', nullable=False),
    sa.Column('content', sa.Text(), server_default='', nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
    sa.Column('inbound_update_id', sa.UUID(), nullable=True),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['inbound_update_id'], ['inbound_updates.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_local_date'), 'messages', ['local_date'], unique=False)
    op.create_index(op.f('ix_messages_user_id'), 'messages', ['user_id'], unique=False)
    op.create_table('outbound_messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('text_body', sa.Text(), nullable=False),
    sa.Column('dedupe_key', sa.Text(), nullable=True),
    sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key')
    )
    op.create_table('telegram_links',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('telegram_user_id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('user_settings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('timezone', sa.Text(), server_default='Europe/London', nullable=False),
    sa.Column('day_end_hour', sa.SmallInteger(), server_default='4', nullable=False),
    sa.Column('quiet_start', sa.Time(), server_default='22:30', nullable=False),
    sa.Column('quiet_end', sa.Time(), server_default='07:00', nullable=False),
    sa.Column('max_proactive_per_day', sa.SmallInteger(), server_default='5', nullable=False),
    sa.Column('monthly_ai_budget_pence', sa.Integer(), server_default='1000', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )

    # Row-level security as a backstop: with RLS on and no policies, Supabase's
    # public roles (anon, authenticated) can read nothing. Our backend connects
    # as the table owner, which bypasses RLS, so FastAPI stays the only data path.
    for table in APP_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table('user_settings')
    op.drop_table('telegram_links')
    op.drop_table('outbound_messages')
    op.drop_index(op.f('ix_messages_user_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_local_date'), table_name='messages')
    op.drop_table('messages')
    op.drop_table('link_codes')
    op.drop_index('ix_jobs_due', table_name='jobs')
    op.drop_table('jobs')
    op.drop_table('audit_events')
    op.drop_table('users')
    op.drop_index(op.f('ix_inbound_updates_telegram_user_id'), table_name='inbound_updates')
    op.drop_table('inbound_updates')
