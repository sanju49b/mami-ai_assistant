"use client";

import { useState } from "react";
import { api, type Expense, type Goal, type Memory, type Task } from "@/lib/api";
import { Bar, Button, catColor, Empty, ErrorNote, minutesLabel, Section, useLoad } from "./ui";

type Target = Goal & { this_week?: { actual: number | null; target: number | null; percent: number | null } };
type WeekData = {
  week: string;
  completed_minutes_by_day: Record<string, Record<string, number>>;
  spend_by_category: Record<string, string>;
  spend_total: string;
  targets: Target[];
};

function targetLine(g: Target) {
  const p = g.this_week;
  if (!p || p.actual == null || p.target == null) return null;
  const fmt = (v: number) =>
    g.metric === "minutes" ? minutesLabel(v) : g.metric === "pence" ? `£${(v / 100).toFixed(2)}` : String(v);
  return `${fmt(p.actual)} of ${fmt(p.target)}`;
}

export function Week() {
  const { data, error } = useLoad(() => api.get<WeekData>("/api/week"));
  const days = data ? Object.entries(data.completed_minutes_by_day) : [];
  const maxDay = Math.max(60, ...days.map(([, cats]) => Object.values(cats).reduce((a, b) => a + b, 0)));
  return (
    <div className="max-w-2xl">
      <h1 className="mb-6 font-display text-3xl font-semibold tracking-tight">This week</h1>
      <ErrorNote error={error} />
      <Section title="Targets">
        {!data || data.targets.length === 0 ? (
          <Empty>No weekly targets yet. Tell Mami one, like “6 hours of DSA a week”, and approve it.</Empty>
        ) : (
          <ul className="flex flex-col gap-4">
            {data.targets.map((g) => (
              <li key={g.id} className="rounded-2xl bg-card p-4 ring-1 ring-line">
                <div className="mb-2 flex justify-between gap-3">
                  <span className="font-semibold">{g.title}</span>
                  <span className="font-display text-sm tabular-nums text-soft">{targetLine(g)}</span>
                </div>
                <Bar value={g.this_week?.actual || 0} max={g.this_week?.target || 1} color={catColor(g.category)} />
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Time by day">
        {days.length === 0 ? (
          <Empty>No completed time logged this week yet.</Empty>
        ) : (
          <ul className="flex flex-col gap-3">
            {days.map(([day, cats]) => {
              const total = Object.values(cats).reduce((a, b) => a + b, 0);
              return (
                <li key={day} className="grid grid-cols-[4.5rem_1fr_4.5rem] items-center gap-3 text-sm">
                  <span className="text-soft">
                    {new Date(day + "T12:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", timeZone: "UTC" })}
                  </span>
                  <span className="flex h-3 overflow-hidden rounded-full bg-[#ece5dc]" style={{ width: `${(100 * total) / maxDay}%` }}>
                    {Object.entries(cats).map(([cat, m]) => (
                      <span key={cat} title={`${cat}: ${minutesLabel(m)}`} style={{ width: `${(100 * m) / total}%`, background: catColor(cat) }} />
                    ))}
                  </span>
                  <span className="text-right font-display tabular-nums">{minutesLabel(total)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </Section>
      {data && (
        <Section title="Spent this week" action={<span className="font-display text-lg font-semibold">{data.spend_total}</span>}>
          {Object.keys(data.spend_by_category).length === 0 ? (
            <Empty>No spending logged this week.</Empty>
          ) : (
            <ul className="divide-y divide-line rounded-2xl bg-card ring-1 ring-line">
              {Object.entries(data.spend_by_category).map(([c, v]) => (
                <li key={c} className="flex justify-between px-4 py-3 text-sm capitalize">
                  <span>{c}</span>
                  <span className="font-display tabular-nums">{v}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}
    </div>
  );
}

export function Tasks() {
  const { data, error, reload, setError } = useLoad(() =>
    api.get<{ tasks: Task[]; inbox: { id: string; kind: string; content: string }[] }>("/api/tasks"),
  );
  async function toggle(t: Task) {
    try {
      await api.patch(`/api/tasks/${t.id}`, { version: t.version, status: t.status === "done" ? "open" : "done" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Didn't save.");
    }
    reload();
  }
  return (
    <div className="max-w-2xl">
      <h1 className="mb-6 font-display text-3xl font-semibold tracking-tight">Tasks</h1>
      <ErrorNote error={error} />
      <Section title="To do">
        {!data || data.tasks.length === 0 ? (
          <Empty>Nothing open. Tell Mami what you need to do.</Empty>
        ) : (
          <ul className="divide-y divide-line rounded-2xl bg-card ring-1 ring-line">
            {data.tasks.map((t) => (
              <li key={t.id}>
                <label className="flex min-h-12 items-center gap-3 px-4 py-2">
                  <input type="checkbox" checked={t.status === "done"} onChange={() => toggle(t)} className="h-5 w-5 accent-[var(--color-ink)]" />
                  <span className="flex-1">
                    {t.title}
                    {t.important && <span className="ml-2 text-xs font-semibold text-[#b3261e]">important</span>}
                  </span>
                  {t.due_date && (
                    <span className="text-xs text-soft">
                      {new Date(t.due_date + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" })}
                      {t.due_time && ` ${t.due_time}`}
                    </span>
                  )}
                </label>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Ideas and notes">
        {!data || data.inbox.length === 0 ? (
          <Empty>Ideas you mention (“maybe start a YouTube channel”) land here, never on your schedule.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.inbox.map((i) => (
              <li key={i.id} className="rounded-2xl bg-card px-4 py-3 text-sm ring-1 ring-line">
                <span className="mr-2 text-xs font-semibold text-soft">{i.kind === "idea" ? "Idea" : "Note"}</span>
                {i.content}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

export function Money() {
  const [days, setDays] = useState(30);
  const { data, error } = useLoad(
    () => api.get<{ expenses: Expense[]; by_category_pence: Record<string, number>; total_pence: number }>(`/api/expenses?days=${days}`),
    [days],
  );
  const cats = data ? Object.entries(data.by_category_pence).sort((a, b) => b[1] - a[1]) : [];
  const max = cats[0]?.[1] || 1;
  return (
    <div className="max-w-2xl">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-semibold tracking-tight">Money</h1>
          {data && <p className="mt-1 text-soft">£{(data.total_pence / 100).toFixed(2)} in the last {days} days</p>}
        </div>
        <label className="text-sm">
          <span className="sr-only">Period</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="min-h-10 rounded-full bg-card px-3 ring-1 ring-line">
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </label>
      </header>
      <ErrorNote error={error} />
      <Section title="By category">
        {cats.length === 0 ? (
          <Empty>No spending yet. Tell Mami “spent £8.50 on lunch”.</Empty>
        ) : (
          <ul className="flex flex-col gap-3">
            {cats.map(([c, p]) => (
              <li key={c} className="grid grid-cols-[6rem_1fr_5rem] items-center gap-3 text-sm">
                <span className="capitalize">{c}</span>
                <Bar value={p} max={max} color={catColor(c)} />
                <span className="text-right font-display tabular-nums">£{(p / 100).toFixed(2)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {data && data.expenses.length > 0 && (
        <Section title="All spending">
          <ul className="divide-y divide-line rounded-2xl bg-card ring-1 ring-line">
            {data.expenses.map((e) => (
              <li key={e.id} className="flex justify-between gap-3 px-4 py-3 text-sm">
                <span>
                  <span className="text-soft">{new Date(e.date + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" })}</span>{" "}
                  <span className="font-semibold capitalize">{e.category}</span>
                  {(e.merchant || e.note) && <span className="text-soft"> {e.merchant || e.note}</span>}
                  {e.worth_it != null && <span className="text-soft"> worth it {e.worth_it}/5</span>}
                </span>
                <span className="font-display tabular-nums">{e.amount}{e.status === "estimated" ? "~" : ""}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

export function Goals() {
  const { data, error } = useLoad(() => api.get<{ goals: Target[] }>("/api/goals"));
  const goals = data?.goals || [];
  const byParent = (pid: string | null) => goals.filter((g) => (g as Goal & { parent_id?: string | null }).parent_id === pid);
  const tops = goals.filter((g) => !(g as Goal & { parent_id?: string | null }).parent_id);
  const kindLabel: Record<string, string> = { long_term: "Goal", milestone: "Milestone", weekly_target: "Weekly target", project: "Project" };
  return (
    <div className="max-w-2xl">
      <h1 className="mb-2 font-display text-3xl font-semibold tracking-tight">Goals</h1>
      <p className="mb-6 text-soft">Goals and targets only change when you approve it.</p>
      <ErrorNote error={error} />
      {tops.length === 0 ? (
        <Empty>No goals yet. Tell Mami what you're working towards, e.g. “get hired at a big tech company”.</Empty>
      ) : (
        <ul className="flex flex-col gap-4">
          {tops.map((g) => (
            <li key={g.id} className="rounded-2xl bg-card p-4 ring-1 ring-line">
              <p className="text-xs font-semibold text-soft">{kindLabel[g.kind]}</p>
              <p className="font-display text-lg font-semibold">{g.title}</p>
              {g.this_week && (
                <div className="mt-2">
                  <Bar value={g.this_week.actual || 0} max={g.this_week.target || 1} color={catColor(g.category)} />
                  <p className="mt-1 text-sm text-soft">{targetLine(g)} this week</p>
                </div>
              )}
              {byParent(g.id).length > 0 && (
                <ul className="mt-3 flex flex-col gap-2 border-l-2 border-line pl-4">
                  {byParent(g.id).map((c) => (
                    <li key={c.id} className="text-sm">
                      <span className="font-semibold">{c.title}</span>
                      {c.deadline && <span className="text-soft"> by {c.deadline}</span>}
                      {c.this_week && <span className="text-soft">, {targetLine(c)}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Settings = { quiet_hours: string; coaching_style: string };

export function Me({ settings, onLogout }: { settings: Settings; onLogout: () => void }) {
  const mem = useLoad(() => api.get<{ memories: Memory[] }>("/api/memories"));
  const [style, setStyle] = useState(settings.coaching_style);
  const [qStart, qEnd] = settings.quiet_hours.split("-");
  const [quietStart, setQuietStart] = useState(qStart);
  const [quietEnd, setQuietEnd] = useState(qEnd);
  const [budget, setBudget] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    try {
      await api.patch("/api/settings", {
        coaching_style: style,
        quiet_start: quietStart,
        quiet_end: quietEnd,
        ...(budget ? { monthly_ai_budget_pence: Math.round(Number(budget) * 100) } : {}),
      });
      setSaved("Settings saved.");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Settings didn't save.");
    }
  }
  async function memAction(fn: () => Promise<unknown>) {
    try {
      await fn();
    } catch (e) {
      mem.setError(e instanceof Error ? e.message : "Didn't save.");
    }
    mem.reload();
  }

  return (
    <div className="max-w-2xl">
      <h1 className="mb-6 font-display text-3xl font-semibold tracking-tight">Me</h1>
      <Section title="What Mami remembers">
        <ErrorNote error={mem.error} />
        {!mem.data || mem.data.memories.length === 0 ? (
          <Empty>Nothing yet. Say “remember I focus best before 9” and it shows up here.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {mem.data.memories.map((m) => (
              <li key={m.id} className="rounded-2xl bg-card p-4 ring-1 ring-line">
                <p className={m.status === "observed" ? "italic" : ""}>{m.content}</p>
                <p className="mt-1 text-xs text-soft">
                  {m.status === "confirmed" ? "Confirmed by you" : "Mami noticed this. Not confirmed yet"}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {m.status === "observed" && (
                    <Button onClick={() => memAction(() => api.patch(`/api/memories/${m.id}`, { version: m.version, status: "confirmed" }))}>
                      That's right
                    </Button>
                  )}
                  <Button
                    onClick={() => {
                      const next = window.prompt("Correct this memory", m.content);
                      if (next && next.trim() && next !== m.content)
                        memAction(() => api.patch(`/api/memories/${m.id}`, { version: m.version, content: next.trim(), status: "confirmed" }));
                    }}
                  >
                    Correct
                  </Button>
                  <Button kind="danger" onClick={() => memAction(() => api.del(`/api/memories/${m.id}`))}>
                    Forget
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="How Mami coaches you">
        <ErrorNote error={error} />
        <div className="flex flex-col gap-4 rounded-2xl bg-card p-4 ring-1 ring-line">
          <fieldset>
            <legend className="mb-2 text-sm font-semibold">Coaching style</legend>
            <div className="flex flex-wrap gap-2">
              {[
                ["gentle", "Gentle"],
                ["adaptive", "Firm on good days, gentle on hard ones"],
                ["firm", "Firm"],
              ].map(([v, label]) => (
                <label key={v} className={`cursor-pointer rounded-full px-4 py-2 text-sm ring-1 ${style === v ? "bg-ink text-white ring-ink" : "ring-line"}`}>
                  <input type="radio" name="style" value={v} checked={style === v} onChange={() => setStyle(v)} className="sr-only" />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">Quiet hours</span>
            <input aria-label="Quiet from" type="time" value={quietStart} onChange={(e) => setQuietStart(e.target.value)} className="rounded-lg px-2 py-1 ring-1 ring-line" />
            <span>to</span>
            <input aria-label="Quiet until" type="time" value={quietEnd} onChange={(e) => setQuietEnd(e.target.value)} className="rounded-lg px-2 py-1 ring-1 ring-line" />
          </div>
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">Monthly AI budget (£)</span>
            <input inputMode="decimal" placeholder="10" value={budget} onChange={(e) => setBudget(e.target.value)} className="w-24 rounded-lg px-2 py-1 ring-1 ring-line" />
          </label>
          <div className="flex items-center gap-3">
            <Button kind="primary" onClick={save}>Save settings</Button>
            {saved && <span className="text-sm text-soft" role="status">{saved}</span>}
          </div>
        </div>
      </Section>
      <Section title="Your data">
        <div className="flex flex-wrap gap-2">
          <a href="/api/export" download="day-manager-export.json" className="inline-flex min-h-10 items-center rounded-full bg-card px-4 text-sm font-semibold ring-1 ring-line">
            Download all my data
          </a>
          <Button kind="danger" onClick={onLogout}>Log out</Button>
        </div>
      </Section>
    </div>
  );
}
