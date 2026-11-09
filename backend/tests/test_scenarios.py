"""The spec's difficult scenarios, end to end through the real code.

The model is scripted (it says which tools to call); everything after that,
validation, rules, database writes, approvals and replies, is the real thing.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app import approvals
from app.agent.tools import ToolContext, execute
from app.common import Conflict, ToolError, get_settings_row, today_for
from app.db import session_scope
from app.jobs import enqueue, run_due_jobs
from app.models import (
    ActivitySession,
    AuditEvent,
    DailyReview,
    Expense,
    Goal,
    InboxItem,
    Job,
    Memory,
    OutboundMessage,
    PendingApproval,
    PlanVersion,
    Task,
    TimeBlock,
    UserSettings,
)
from app.reviews import close_day
from app.timeutil import utc_now
from tests.conftest_helpers import CHAT, agent, call, count, make_linked_user
from tests.helpers import SECRET_HEADERS


@pytest.fixture
def uid():
    return make_linked_user()


def ctx_for(s, uid, now=None, msg=None) -> ToolContext:
    return ToolContext(session=s, user_id=uid, settings=get_settings_row(s, uid),
                       now=now or utc_now(), source_message_id=msg)


# 1 -------------------------------------------------------------------------
def test_one_message_with_several_activities_expenses_and_tasks(llm, uid):
    result = agent(
        llm, uid,
        "Bro, I finished 45 minutes of DSA, spent £12 on lunch, skipped the gym because I was "
        "exhausted, and I need to buy groceries tomorrow.",
        call("log_activity", title="DSA practice", category="study", status="completed", minutes=45),
        call("log_expense", amount=12, category="food", estimated=False, note="lunch"),
        call("log_activity", title="Gym", category="exercise", status="missed",
             notes="was exhausted"),
        call("create_task", title="Buy groceries", due_date="tomorrow"),
        reply="Nice work on DSA. Rest up.",
    )
    with session_scope() as s:
        acts = {a.title: a for a in s.execute(select(ActivitySession)).scalars()}
        assert acts["DSA practice"].minutes == 45 and acts["DSA practice"].status == "completed"
        assert acts["Gym"].status == "missed" and acts["Gym"].minutes is None
        assert acts["Gym"].notes == "was exhausted"
        assert s.execute(select(Expense)).scalar_one().amount_pence == 1200
        task = s.execute(select(Task)).scalar_one()
        today = today_for(get_settings_row(s, uid), utc_now())
        assert task.due_date == today + timedelta(days=1)
    # The reply's receipt comes from what was really saved.
    assert "✓ Logged DSA practice (completed, 45 min" in result.reply
    assert "✓ Spent £12.00 on food" in result.reply
    assert result.reply.startswith("Nice work on DSA.")


# 2 -------------------------------------------------------------------------
def test_expense_without_amount_is_rejected_and_nothing_saved(llm, uid):
    result = agent(llm, uid, "spent a bit on coffee",
                   call("log_expense", category="food", estimated=True),
                   reply="How much was the coffee?")
    assert count(Expense) == 0
    assert "✓" not in result.reply  # no false receipt
    # The model was told about the validation error.
    tool_results = llm.calls[-1]["messages"][-1]["content"]
    assert tool_results[0]["is_error"] is True


def test_negative_or_zero_amounts_are_rejected(uid):
    with session_scope() as s:
        with pytest.raises(ToolError):
            execute(ctx_for(s, uid), "log_expense", {"amount": 0, "category": "food",
                                                     "estimated": False})


def test_minutes_cannot_be_invented_from_nothing(uid):
    """Duration only from the user's words or from stated start/end times."""
    with session_scope() as s:
        out = execute(ctx_for(s, uid), "log_activity",
                      {"title": "Python", "category": "study", "status": "completed",
                       "start_time": "07:00", "end_time": "07:40"})
        assert out["activity"]["minutes"] == 40


# 3 -------------------------------------------------------------------------
def test_correction_updates_in_place_with_audit_and_no_duplicate(llm, uid):
    y = (date.today() - timedelta(days=1)).isoformat()
    agent(llm, uid, "did 60 min python yesterday",
          call("log_activity", title="Python assignment", category="study", status="completed",
               minutes=60, date=y))
    with session_scope() as s:
        act = s.execute(select(ActivitySession)).scalar_one()
        act_id = str(act.id)
    agent(llm, uid, "Actually, I only did 30 minutes of Python yesterday, then went out.",
          call("correct_activity", activity_id=act_id, minutes=30))
    with session_scope() as s:
        acts = s.execute(select(ActivitySession)).scalars().all()
        assert len(acts) == 1 and acts[0].minutes == 30 and acts[0].version == 2
        ev = s.execute(select(AuditEvent).where(AuditEvent.entity_type == "activity_sessions",
                                                AuditEvent.action == "update")).scalar_one()
        assert ev.before["minutes"] == 60 and ev.after["minutes"] == 30


def test_retried_message_does_not_duplicate_records(llm, uid):
    msg = uuid.uuid4()
    for _ in range(2):  # the same message processed twice (job retry)
        agent(llm, uid, "spent £5 on bus", call("log_expense", amount=5, category="transport",
                                                estimated=False), source_message_id=msg)
    assert count(Expense) == 1


# 4 + 14 ----------------------------------------------------------------------
def _approved_plan(uid, day: date, blocks):
    with session_scope() as s:
        approvals.new_plan_version(s, uuid.UUID(str(uid)), day, blocks, None, utc_now())


def _block(title, start: datetime, hours=1, **kw):
    return {"title": title, "category": kw.get("category", "study"),
            "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=hours)).isoformat(),
            "important": kw.get("important", True), "checklist": []}


def test_unreported_and_day_off_become_unknown_never_missed(llm, uid):
    day = date(2026, 11, 3)
    start = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
    _approved_plan(uid, day, [_block("DSA", start), _block("Gym", start + timedelta(hours=10),
                                                           category="exercise")])
    with session_scope() as s:
        review = close_day(s, uuid.UUID(str(uid)), day, utc_now())
        assert review.stats["blocks"] == {"unknown": 2}
        assert "missed" not in str(review.stats["blocks"])
        statuses = {b.status for b in s.execute(select(TimeBlock)).scalars()}
        assert statuses == {"unknown"}


def test_quiet_day_with_nothing_planned_is_still_saved(uid):
    with session_scope() as s:
        review = close_day(s, uuid.UUID(str(uid)), date(2026, 11, 4), utc_now())
        assert review.summary.startswith("No plan and nothing logged")
    assert count(DailyReview) == 1


# 5, 6, 12 --------------------------------------------------------------------
def _press(client, approval_id, decision, cb_id):
    update = {
        "update_id": abs(hash(cb_id)) % 10**9,
        "callback_query": {"id": cb_id, "from": {"id": CHAT, "is_bot": False, "first_name": "S"},
                           "data": f"ap:{approval_id}:{decision}"},
    }
    return client.post("/telegram/webhook", json=update, headers=SECRET_HEADERS).json()["status"]


def test_reschedule_needs_approval_then_runs_once(client, telegram, llm, uid):
    with session_scope() as s:
        t = Task(user_id=uid, title="Call parents", due_date=date(2026, 11, 14))
        s.add(t)
        s.flush()
        task_id = str(t.id)
    result = agent(llm, uid, "move calling parents to Sunday",
                   call("propose_reschedule", target_type="task", target_id=task_id,
                        new_date="2026-11-15"))
    approval = result.approvals[0]
    with session_scope() as s:
        assert s.get(Task, uuid.UUID(task_id)).due_date == date(2026, 11, 14)  # unchanged
    assert _press(client, approval.id, "a", "cb1") == "approval_decision"
    assert _press(client, approval.id, "a", "cb2") == "approval_decision"  # double tap
    run_due_jobs()
    with session_scope() as s:
        assert s.get(Task, uuid.UUID(task_id)).due_date == date(2026, 11, 15)
        assert s.get(PendingApproval, approval.id).status == "executed"
    texts = [t for _, t in telegram.sent]
    assert any(t.startswith("Done:") for t in texts)
    assert any("Already handled" in t for t in texts)


def test_rejected_approval_changes_nothing(client, telegram, llm, uid):
    with session_scope() as s:
        g = Goal(user_id=uid, kind="weekly_target", title="DSA", category="study",
                 metric="minutes", target_value=360, period="week")
        s.add(g)
        s.flush()
        goal_id = str(g.id)
    result = agent(llm, uid, "lower my DSA target to 3 hours",
                   call("propose_goal_change", goal_id=goal_id, target_value=180,
                        reason="He asked"))
    _press(client, result.approvals[0].id, "r", "cb3")
    run_due_jobs()
    with session_scope() as s:
        g = s.get(Goal, uuid.UUID(goal_id))
        assert float(g.target_value) == 360 and g.version == 1
        assert s.get(PendingApproval, result.approvals[0].id).status == "rejected"


def test_approved_goal_change_applies_and_stale_one_is_refused(llm, uid):
    with session_scope() as s:
        g = Goal(user_id=uid, kind="weekly_target", title="DSA", category="study",
                 metric="minutes", target_value=360, period="week")
        s.add(g)
        s.flush()
        goal_id = g.id
    first = agent(llm, uid, "raise to 7h", call("propose_goal_change", goal_id=str(goal_id),
                                                target_value=420, reason="asked"))
    with session_scope() as s:
        out = approvals.decide(s, uuid.UUID(str(uid)), first.approvals[0].id, "approve", utc_now())
        assert out["status"] == "executed"
        assert float(s.get(Goal, goal_id).target_value) == 420
    # A proposal made against version 1 must not apply on version 2.
    with session_scope() as s:
        stale = PendingApproval(user_id=uid, action_type="update_goal",
                                payload={"goal_id": str(goal_id), "changes": {"target_value": 100}},
                                summary="x", target_version=1,
                                expires_at=utc_now() + timedelta(hours=1))
        s.add(stale)
        s.flush()
        out = approvals.decide(s, uuid.UUID(str(uid)), stale.id, "approve", utc_now())
        assert out["status"] == "stale"
        assert float(s.get(Goal, goal_id).target_value) == 420


def test_new_targets_need_approval(llm, uid):
    agent(llm, uid, "I want 6 hours of DSA a week",
          call("propose_goal", kind="weekly_target", title="DSA 6h/week", category="study",
               metric="minutes", target_value=360, period="week"))
    assert count(Goal) == 0
    assert count(PendingApproval, PendingApproval.status == "pending") == 1


# 8 -------------------------------------------------------------------------
def test_stale_concurrent_update_is_rejected(uid):
    with session_scope() as s:
        a = ActivitySession(user_id=uid, title="Python", category="study", local_date=date.today(),
                            minutes=30, status="completed")
        s.add(a)
        s.flush()
        act_id = str(a.id)
    with session_scope() as s:
        execute(ctx_for(s, uid), "correct_activity",
                {"activity_id": act_id, "minutes": 40, "expected_version": 1})
    with session_scope() as s:
        with pytest.raises(Conflict):
            execute(ctx_for(s, uid), "correct_activity",
                    {"activity_id": act_id, "minutes": 50, "expected_version": 1})


# 10 ------------------------------------------------------------------------
def test_reminder_in_quiet_hours_is_deferred_not_sent(telegram, uid):
    with session_scope() as s:
        st = s.get(UserSettings, uuid.UUID(str(uid)))
        st.quiet_start, st.quiet_end = time(0, 0), time(23, 59)  # always quiet
        t = Task(user_id=uid, title="Take out bins", due_at=utc_now())
        s.add(t)
        s.flush()
        enqueue(s, "reminder", "reminder:test", payload={"task_id": str(t.id)}, user_id=uid)
    assert run_due_jobs()["deferred"] == 1
    assert telegram.sent == []
    with session_scope() as s:
        job = s.execute(select(Job).where(Job.dedupe_key == "reminder:test")).scalar_one()
        assert job.status == "pending" and job.attempts == 0 and job.run_at > utc_now()


def test_daily_cap_stops_extra_proactive_messages(telegram, uid):
    from app.notify import check_gates

    with session_scope() as s:
        st = s.get(UserSettings, uuid.UUID(str(uid)))
        st.quiet_start = st.quiet_end = time(0, 0)  # no quiet hours
        st.max_proactive_per_day = 1
        s.add(OutboundMessage(user_id=uid, chat_id=CHAT, kind="proactive", text_body="x",
                              status="sent"))
    with session_scope() as s:
        assert check_gates(s, uuid.UUID(str(uid)), utc_now()).action == "skip"


# 13 ------------------------------------------------------------------------
def test_task_from_several_days_ago_can_be_found_and_completed(llm, uid):
    with session_scope() as s:
        t = Task(user_id=uid, title="Python assignment",
                 created_at=utc_now() - timedelta(days=5))
        s.add(t)
        s.flush()
        task_id = str(t.id)
    with session_scope() as s:
        found = execute(ctx_for(s, uid), "search_records", {"query": "python", "kind": "task"})
        assert found["tasks"][0]["id"] == task_id
    agent(llm, uid, "I already finished the python assignment",
          call("set_task_status", task_id=task_id, status="done"))
    with session_scope() as s:
        assert s.get(Task, uuid.UUID(task_id)).status == "done"


# 15 ------------------------------------------------------------------------
def test_memory_correction_replaces_old_memory(llm, uid):
    agent(llm, uid, "remember I focus best before 9",
          call("save_memory", content="Focuses best before 9am", kind="preference",
               user_said_explicitly=True))
    with session_scope() as s:
        mem_id = str(s.execute(select(Memory)).scalar_one().id)
    agent(llm, uid, "actually it's before 10",
          call("forget_memory", memory_id=mem_id, replacement="Focuses best before 10am"))
    with session_scope() as s:
        live = s.execute(select(Memory).where(Memory.deleted_at.is_(None))).scalars().all()
        assert [m.content for m in live] == ["Focuses best before 10am"]
        assert live[0].status == "confirmed"


def test_inferred_memory_is_only_an_observation(llm, uid):
    agent(llm, uid, "...", call("save_memory", content="Skips gym on Mondays", kind="pattern",
                                user_said_explicitly=False))
    with session_scope() as s:
        assert s.execute(select(Memory)).scalar_one().status == "observed"


# Ideas, undo, plans, budget -----------------------------------------------------
def test_casual_idea_is_not_scheduled(llm, uid):
    agent(llm, uid, "maybe start a YouTube channel",
          call("create_inbox_item", kind="idea", content="Start a YouTube channel"))
    assert count(InboxItem) == 1 and count(Task) == 0


def test_undo_reverses_the_last_change(llm, uid):
    agent(llm, uid, "£8 lunch", call("log_expense", amount=8, category="food", estimated=False))
    agent(llm, uid, "undo", call("undo_last"))
    assert count(Expense, Expense.deleted_at.is_(None)) == 0


def test_plan_overlaps_are_refused_and_revisions_keep_the_original(llm, uid):
    with session_scope() as s:
        with pytest.raises(ToolError, match="overlap"):
            execute(ctx_for(s, uid), "propose_plan", {"date": "2026-11-20", "blocks": [
                {"title": "DSA", "category": "study", "start_time": "07:00", "end_time": "08:00"},
                {"title": "Commute", "category": "commute", "start_time": "07:30",
                 "end_time": "08:30"}]})
    plan = [{"title": "DSA", "category": "study", "start_time": "07:00", "end_time": "08:00",
             "important": True, "checklist": ["Arrays recap", "2 NeetCode problems"]},
            {"title": "Office", "category": "work", "start_time": "09:00", "end_time": "17:00"}]
    r1 = agent(llm, uid, "plan friday", call("propose_plan", date="2026-11-20", blocks=plan))
    with session_scope() as s:
        approvals.decide(s, uuid.UUID(str(uid)), r1.approvals[0].id, "approve", utc_now())
    plan[0]["start_time"], plan[0]["end_time"] = "06:30", "07:30"
    r2 = agent(llm, uid, "move DSA earlier", call("propose_plan", date="2026-11-20", blocks=plan))
    with session_scope() as s:
        out2 = approvals.decide(s, uuid.UUID(str(uid)), r2.approvals[0].id, "approve", utc_now())
        assert out2["status"] == "executed", out2
        versions = s.execute(select(PlanVersion).order_by(PlanVersion.version_no)).scalars().all()
        assert [v.version_no for v in versions] == [1, 2]
    assert count(TimeBlock) == 4  # 2 blocks x 2 versions: the original is kept


def test_ai_budget_cap_blocks_model_calls(llm, uid):
    from app.models import LLMUsage

    with session_scope() as s:
        s.add(LLMUsage(user_id=uid, model="claude-sonnet-5-5", purpose="x", input_tokens=0,
                       output_tokens=0, cost_usd_micros=50_000_000))  # $50
    result = agent(llm, uid, "hi")
    assert result.budget_blocked and llm.calls == []
