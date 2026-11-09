"""assistant tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 13:43:24.768487
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

NEW_TABLES = ['daily_plans', 'daily_reviews', 'expenses', 'goals', 'inbox_items', 'integration_connections', 'llm_usage', 'login_codes', 'memories', 'oauth_states', 'pending_approvals', 'web_sessions', 'whoop_days', 'whoop_workouts', 'plan_versions', 'tasks', 'activity_sessions', 'time_blocks', 'focus_sessions']


def upgrade() -> None:
    op.create_table('daily_plans',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('current_version_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'local_date', name='uq_plan_day')
    )
    op.create_index(op.f('ix_daily_plans_user_id'), 'daily_plans', ['user_id'], unique=False)
    op.create_table('daily_reviews',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('finalized_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'local_date', name='uq_review_day')
    )
    op.create_index(op.f('ix_daily_reviews_user_id'), 'daily_reviews', ['user_id'], unique=False)
    op.create_table('expenses',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('amount_pence', sa.Integer(), nullable=False),
    sa.Column('currency', sa.Text(), server_default='GBP', nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('merchant', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('status', sa.Text(), server_default='confirmed', nullable=False),
    sa.Column('worth_it', sa.SmallInteger(), nullable=True),
    sa.Column('source_message_id', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('confirmed','estimated')", name='expense_status'),
    sa.CheckConstraint('amount_pence > 0', name='expense_amount_positive'),
    sa.CheckConstraint('worth_it is null or worth_it between 1 and 5', name='expense_worth_it'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_expenses_local_date'), 'expenses', ['local_date'], unique=False)
    op.create_index(op.f('ix_expenses_user_id'), 'expenses', ['user_id'], unique=False)
    op.create_table('goals',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('metric', sa.Text(), nullable=True),
    sa.Column('target_value', sa.Numeric(), nullable=True),
    sa.Column('period', sa.Text(), nullable=True),
    sa.Column('deadline', sa.Date(), nullable=True),
    sa.Column('status', sa.Text(), server_default='active', nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind in ('long_term','milestone','weekly_target','project')", name='goal_kind'),
    sa.CheckConstraint("status in ('active','done','archived')", name='goal_status'),
    sa.ForeignKeyConstraint(['parent_id'], ['goals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_goals_user_id'), 'goals', ['user_id'], unique=False)
    op.create_table('inbox_items',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source_message_id', sa.UUID(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind in ('idea','note')", name='inbox_kind'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_inbox_items_user_id'), 'inbox_items', ['user_id'], unique=False)
    op.create_table('integration_connections',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('access_token_enc', sa.Text(), nullable=False),
    sa.Column('refresh_token_enc', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scopes', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='active', nullable=False),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'provider', name='uq_user_provider')
    )
    op.create_index(op.f('ix_integration_connections_user_id'), 'integration_connections', ['user_id'], unique=False)
    op.create_table('llm_usage',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cost_usd_micros', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_llm_usage_user_id'), 'llm_usage', ['user_id'], unique=False)
    op.create_table('login_codes',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('code_hash', sa.Text(), nullable=False),
    sa.Column('attempts', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_login_codes_user_id'), 'login_codes', ['user_id'], unique=False)
    op.create_table('memories',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), server_default='fact', nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Numeric(), nullable=True),
    sa.Column('source_message_id', sa.UUID(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('observed','confirmed','rejected')", name='memory_status'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memories_user_id'), 'memories', ['user_id'], unique=False)
    op.create_table('oauth_states',
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('state')
    )
    op.create_index(op.f('ix_oauth_states_user_id'), 'oauth_states', ['user_id'], unique=False)
    op.create_table('pending_approvals',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('action_type', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('target_version', sa.Integer(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('pending','executing','executed','rejected','expired','stale','failed')", name='approval_status'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pending_approvals_user_id'), 'pending_approvals', ['user_id'], unique=False)
    op.create_table('web_sessions',
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_web_sessions_user_id'), 'web_sessions', ['user_id'], unique=False)
    op.create_table('whoop_days',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('cycle_id', sa.Text(), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('cycle_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cycle_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('strain', sa.Numeric(), nullable=True),
    sa.Column('kilojoule', sa.Numeric(), nullable=True),
    sa.Column('average_heart_rate', sa.Integer(), nullable=True),
    sa.Column('max_heart_rate', sa.Integer(), nullable=True),
    sa.Column('step_count', sa.Integer(), nullable=True),
    sa.Column('recovery_score', sa.Numeric(), nullable=True),
    sa.Column('hrv_rmssd_milli', sa.Numeric(), nullable=True),
    sa.Column('resting_heart_rate', sa.Numeric(), nullable=True),
    sa.Column('sleep_performance', sa.Numeric(), nullable=True),
    sa.Column('sleep_hours', sa.Numeric(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'cycle_id', name='uq_whoop_cycle')
    )
    op.create_index(op.f('ix_whoop_days_local_date'), 'whoop_days', ['local_date'], unique=False)
    op.create_index(op.f('ix_whoop_days_user_id'), 'whoop_days', ['user_id'], unique=False)
    op.create_table('whoop_workouts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('workout_id', sa.Text(), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('sport_name', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('strain', sa.Numeric(), nullable=True),
    sa.Column('average_heart_rate', sa.Integer(), nullable=True),
    sa.Column('max_heart_rate', sa.Integer(), nullable=True),
    sa.Column('kilojoule', sa.Numeric(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'workout_id', name='uq_whoop_workout')
    )
    op.create_index(op.f('ix_whoop_workouts_local_date'), 'whoop_workouts', ['local_date'], unique=False)
    op.create_index(op.f('ix_whoop_workouts_user_id'), 'whoop_workouts', ['user_id'], unique=False)
    op.create_table('plan_versions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['daily_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_id', 'version_no', name='uq_plan_version')
    )
    op.create_table('tasks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), server_default='task', nullable=False),
    sa.Column('status', sa.Text(), server_default='open', nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('important', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('source_message_id', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind in ('task','reminder','commitment')", name='task_kind'),
    sa.CheckConstraint("status in ('open','done','cancelled')", name='task_status'),
    sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tasks_user_id'), 'tasks', ['user_id'], unique=False)
    op.create_table('activity_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('minutes', sa.Integer(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), server_default='chat', nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('goal_id', sa.UUID(), nullable=True),
    sa.Column('block_id', sa.UUID(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('source_message_id', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('planned','in_progress','completed','missed','cancelled','unknown')", name='activity_status'),
    sa.CheckConstraint('minutes is null or minutes >= 0', name='activity_minutes'),
    sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_activity_sessions_local_date'), 'activity_sessions', ['local_date'], unique=False)
    op.create_index(op.f('ix_activity_sessions_user_id'), 'activity_sessions', ['user_id'], unique=False)
    op.create_table('time_blocks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('plan_version_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('important', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('status', sa.Text(), server_default='planned', nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('goal_id', sa.UUID(), nullable=True),
    sa.Column('checklist', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.CheckConstraint("status in ('planned','in_progress','completed','missed','cancelled','unknown')", name='block_status'),
    sa.CheckConstraint('ends_at > starts_at', name='block_order'),
    sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['plan_version_id'], ['plan_versions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_time_blocks_plan_version_id'), 'time_blocks', ['plan_version_id'], unique=False)
    op.create_index(op.f('ix_time_blocks_user_id'), 'time_blocks', ['user_id'], unique=False)
    op.create_table('focus_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('goal_id', sa.UUID(), nullable=True),
    sa.Column('work_minutes', sa.SmallInteger(), server_default='25', nullable=False),
    sa.Column('break_minutes', sa.SmallInteger(), server_default='5', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paused_seconds', sa.Integer(), server_default='0', nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('activity_session_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("status in ('running','paused','completed','stopped')", name='focus_status'),
    sa.ForeignKeyConstraint(['activity_session_id'], ['activity_sessions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('activity_session_id')
    )
    op.create_index(op.f('ix_focus_sessions_user_id'), 'focus_sessions', ['user_id'], unique=False)
    op.add_column('user_settings', sa.Column('morning_brief_time', sa.Time(), server_default='07:30', nullable=False))
    op.add_column('user_settings', sa.Column('evening_review_time', sa.Time(), server_default='21:30', nullable=False))
    op.add_column('user_settings', sa.Column('proactive_cooldown_minutes', sa.SmallInteger(), server_default='60', nullable=False))
    op.add_column('user_settings', sa.Column('coaching_style', sa.Text(), server_default='adaptive', nullable=False))

    for table in NEW_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')

def downgrade() -> None:
    op.drop_column('user_settings', 'coaching_style')
    op.drop_column('user_settings', 'proactive_cooldown_minutes')
    op.drop_column('user_settings', 'evening_review_time')
    op.drop_column('user_settings', 'morning_brief_time')
    op.drop_index(op.f('ix_focus_sessions_user_id'), table_name='focus_sessions')
    op.drop_table('focus_sessions')
    op.drop_index(op.f('ix_time_blocks_user_id'), table_name='time_blocks')
    op.drop_index(op.f('ix_time_blocks_plan_version_id'), table_name='time_blocks')
    op.drop_table('time_blocks')
    op.drop_index(op.f('ix_activity_sessions_user_id'), table_name='activity_sessions')
    op.drop_index(op.f('ix_activity_sessions_local_date'), table_name='activity_sessions')
    op.drop_table('activity_sessions')
    op.drop_index(op.f('ix_tasks_user_id'), table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('plan_versions')
    op.drop_index(op.f('ix_whoop_workouts_user_id'), table_name='whoop_workouts')
    op.drop_index(op.f('ix_whoop_workouts_local_date'), table_name='whoop_workouts')
    op.drop_table('whoop_workouts')
    op.drop_index(op.f('ix_whoop_days_user_id'), table_name='whoop_days')
    op.drop_index(op.f('ix_whoop_days_local_date'), table_name='whoop_days')
    op.drop_table('whoop_days')
    op.drop_index(op.f('ix_web_sessions_user_id'), table_name='web_sessions')
    op.drop_table('web_sessions')
    op.drop_index(op.f('ix_pending_approvals_user_id'), table_name='pending_approvals')
    op.drop_table('pending_approvals')
    op.drop_index(op.f('ix_oauth_states_user_id'), table_name='oauth_states')
    op.drop_table('oauth_states')
    op.drop_index(op.f('ix_memories_user_id'), table_name='memories')
    op.drop_table('memories')
    op.drop_index(op.f('ix_login_codes_user_id'), table_name='login_codes')
    op.drop_table('login_codes')
    op.drop_index(op.f('ix_llm_usage_user_id'), table_name='llm_usage')
    op.drop_table('llm_usage')
    op.drop_index(op.f('ix_integration_connections_user_id'), table_name='integration_connections')
    op.drop_table('integration_connections')
    op.drop_index(op.f('ix_inbox_items_user_id'), table_name='inbox_items')
    op.drop_table('inbox_items')
    op.drop_index(op.f('ix_goals_user_id'), table_name='goals')
    op.drop_table('goals')
    op.drop_index(op.f('ix_expenses_user_id'), table_name='expenses')
    op.drop_index(op.f('ix_expenses_local_date'), table_name='expenses')
    op.drop_table('expenses')
    op.drop_index(op.f('ix_daily_reviews_user_id'), table_name='daily_reviews')
    op.drop_table('daily_reviews')
    op.drop_index(op.f('ix_daily_plans_user_id'), table_name='daily_plans')
    op.drop_table('daily_plans')
