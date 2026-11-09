"use client";

import { useEffect, useState } from "react";
import { api, type Block, type Day, type Focus } from "@/lib/api";
import { Timeline } from "./Timeline";
import { Button, catColor, Empty, ErrorNote, minutesLabel, Section, STATUS_LABEL, useLoad } from "./ui";

function addDays(iso: string, n: number) {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function prettyDate(iso: string) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

function greeting(): string {
  const h = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", hour: "2-digit", hour12: false }).format(new Date()),
  );
  if (h < 5) return "Still up";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function DaySummary({ day }: { day: Day }) {
  const planned = day.blocks.filter((b) => b.status !== "cancelled");
  const done = planned.filter((b) => b.status === "completed").length;
  const pct = planned.length ? done / planned.length : 0;
  const rec = "recovery_score" in day.health ? day.health.recovery_score : null;
  const items: [string, string][] = [
    ["Planned blocks done", planned.length ? `${done} of ${planned.length}` : "No plan yet"],
    ["Spent", day.spent_total],
    ["Recovery", rec != null ? `${Math.round(rec)}%` : "–"],
  ];
  return (
    <div className="mb-8 rounded-3xl bg-card p-5 ring-1 ring-line">
      <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-[#ece5dc]" aria-hidden>
        <div className="h-full rounded-full bg-accent transition-[width] duration-700" style={{ width: `${pct * 100}%` }} />
      </div>
      <dl className="grid grid-cols-3 gap-3">
        {items.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-soft">{k}</dt>
            <dd className="mt-0.5 font-display text-xl font-semibold tabular-nums sm:text-2xl">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function Today({ today, name }: { today: string; name: string }) {
  const [date, setDate] = useState(today);
  const { data, error, reload, setError } = useLoad(() => api.get<Day>(`/api/day?date=${date}`), [date]);
  const [selected, setSelected] = useState<string | null>(null);
  const block = data?.blocks.find((b) => b.id === selected) || null;

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
      await reload();
    }
  }

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_23rem] lg:gap-12">
      <div>
        <header className="mb-5 flex items-end justify-between gap-4">
          <div>
            <p className="mb-1 text-soft">{date === today ? `${greeting()}, ${name}.` : "Looking at another day."}</p>
            <h1 className="font-display text-[2.1rem] font-semibold leading-[1.05] tracking-tight sm:text-5xl">
              {prettyDate(date)}
            </h1>
            {data?.plan && data.plan.versions > 1 && (
              <p className="mt-2 text-sm text-soft">Plan revised {data.plan.versions - 1}× today</p>
            )}
          </div>
          <div className="flex shrink-0 gap-1">
            <button aria-label="Previous day" onClick={() => setDate(addDays(date, -1))} className="h-10 w-10 rounded-full bg-card ring-1 ring-line hover:bg-[#f3eee7]">
              ‹
            </button>
            {date !== today && (
              <button onClick={() => setDate(today)} className="h-10 rounded-full bg-card px-3 text-sm font-semibold ring-1 ring-line">
                Today
              </button>
            )}
            <button aria-label="Next day" onClick={() => setDate(addDays(date, 1))} className="h-10 w-10 rounded-full bg-card ring-1 ring-line hover:bg-[#f3eee7]">
              ›
            </button>
          </div>
        </header>

        {data && <DaySummary day={data} />}

        <ErrorNote error={error} />

        {data && data.approvals.length > 0 && (
          <Section title={data.approvals.length === 1 ? "Waiting for your OK" : `${data.approvals.length} proposals waiting`}>
            <ul className="flex flex-col gap-3">
              {data.approvals.map((a) => (
                <li key={a.id} className="rounded-2xl border-l-4 border-accent bg-card p-4 ring-1 ring-line">
                  <p className="mb-1 text-xs text-soft">Mami suggests</p>
                  <p className="whitespace-pre-line leading-relaxed">{a.summary}</p>
                  <div className="mt-3 flex gap-2">
                    <Button kind="primary" onClick={() => act(() => api.post(`/api/approvals/${a.id}`, { decision: "approve" }))}>
                      Approve
                    </Button>
                    <Button onClick={() => act(() => api.post(`/api/approvals/${a.id}`, { decision: "reject" }))}>Reject</Button>
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {data && (
          <Section title="Plan">
            {data.blocks.length === 0 ? (
              <Empty>No plan for this day yet. Tell Mami “plan my day” on Telegram and approve what she suggests.</Empty>
            ) : (
              <Timeline
                blocks={data.blocks}
                isToday={date === today}
                selectedId={selected}
                onSelect={(b) => setSelected(b.id === selected ? null : b.id)}
              />
            )}
          </Section>
        )}
      </div>

      <aside>
        {block && <BlockPanel key={block.id} block={block} act={act} focusRunning={!!data?.focus} />}
        {data && <FocusPanel focus={data.focus} act={act} />}
        {data && (
          <Section title="What happened">
            {data.activities.length === 0 ? (
              <Empty>Nothing logged yet. Tell Mami what you did, in your own words.</Empty>
            ) : (
              <ul className="flex flex-col gap-2">
                {data.activities.map((a) => (
                  <li key={a.id} className="flex items-center gap-3 rounded-2xl bg-card px-4 py-3 ring-1 ring-line">
                    <span className="h-8 w-1.5 rounded-full" style={{ background: catColor(a.category) }} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-semibold">{a.title}</span>
                      <span className="block text-xs text-soft">
                        {STATUS_LABEL[a.status]}
                        {a.minutes != null && `, ${minutesLabel(a.minutes)}`}
                        {a.notes && `, “${a.notes}”`}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        )}
        {data && <HealthPanel day={data} />}
        {data && (
          <Section title="Spent" action={<span className="font-display text-lg font-semibold">{data.spent_total}</span>}>
            {data.expenses.length === 0 ? (
              <Empty>No spending logged.</Empty>
            ) : (
              <ul className="divide-y divide-line rounded-2xl bg-card ring-1 ring-line">
                {data.expenses.map((e) => (
                  <li key={e.id} className="flex justify-between gap-3 px-4 py-3 text-sm">
                    <span>
                      <span className="font-semibold capitalize">{e.category}</span>
                      {e.note && <span className="text-soft"> {e.note}</span>}
                      {e.status === "estimated" && <span className="text-soft"> (estimate)</span>}
                    </span>
                    <span className="font-display tabular-nums">{e.amount}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        )}
        {data?.review?.summary && (
          <Section title="Day summary">
            <p className="rounded-2xl bg-card p-4 text-sm leading-relaxed ring-1 ring-line">{data.review.summary}</p>
          </Section>
        )}
      </aside>
    </div>
  );
}

function BlockPanel({
  block,
  act,
  focusRunning,
}: {
  block: Block;
  act: (fn: () => Promise<unknown>) => Promise<void>;
  focusRunning: boolean;
}) {
  const patch = (body: object) => act(() => api.patch(`/api/blocks/${block.id}`, { version: block.version, ...body }));
  const statuses: [string, string][] = [
    ["completed", "Done"],
    ["in_progress", "Doing it"],
    ["missed", "Skipped"],
    ["planned", "Reset"],
  ];
  return (
    <Section title={block.title}>
      <div className="rounded-2xl bg-card p-4 ring-1 ring-line">
        <p className="text-sm text-soft">
          {block.start}–{block.end}, {STATUS_LABEL[block.status].toLowerCase()}
        </p>
        {block.checklist.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1">
            {block.checklist.map((item, i) => (
              <li key={i}>
                <label className="flex min-h-10 items-center gap-3">
                  <input
                    type="checkbox"
                    checked={item.done}
                    onChange={() =>
                      patch({ checklist: block.checklist.map((c, j) => (j === i ? { ...c, done: !c.done } : c)) })
                    }
                    className="h-5 w-5 accent-[var(--color-ink)]"
                  />
                  <span className={item.done ? "text-soft line-through" : ""}>{item.text}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {statuses.map(([s, label]) => (
            <Button key={s} kind={block.status === s ? "primary" : "quiet"} onClick={() => patch({ status: s })}>
              {label}
            </Button>
          ))}
          {!focusRunning && (
            <Button onClick={() => act(() => api.post("/api/focus", { title: block.title, category: block.category }))}>
              Start focus
            </Button>
          )}
        </div>
      </div>
    </Section>
  );
}

function FocusPanel({ focus, act }: { focus: Focus | null; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [tick, setTick] = useState(0);
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState(25);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (focus?.status !== "running") return;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [focus?.status]);

  if (!focus) {
    if (!open) {
      return (
        <div className="mb-7">
          <button onClick={() => setOpen(true)} className="flex w-full items-center gap-3 rounded-2xl bg-card px-4 py-3.5 text-left ring-1 ring-line hover:bg-[#f3eee7]">
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[#f5e4dc] text-accent" aria-hidden>
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="12" cy="13" r="7" /><path d="M12 9.5V13l2.5 1.5M10 3h4" /></svg>
            </span>
            <span>
              <span className="block font-semibold">Start a focus session</span>
              <span className="block text-sm text-soft">25, 50 or 90 minutes. Logged when you finish.</span>
            </span>
          </button>
        </div>
      );
    }
    return (
      <Section title="Focus">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (title.trim()) act(() => api.post("/api/focus", { title, category: "study", work_minutes: minutes }));
          }}
        >
          <label className="sr-only" htmlFor="focus-title">What are you focusing on?</label>
          <input
            id="focus-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What are you focusing on?"
            className="min-h-10 flex-1 rounded-full bg-card px-4 text-sm ring-1 ring-line"
          />
          <label className="sr-only" htmlFor="focus-len">Length</label>
          <select id="focus-len" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className="min-h-10 rounded-full bg-card px-3 text-sm ring-1 ring-line">
            {[25, 50, 90].map((m) => (
              <option key={m} value={m}>{m} min</option>
            ))}
          </select>
          <Button kind="primary" type="submit" disabled={!title.trim()}>Start</Button>
        </form>
      </Section>
    );
  }
  const elapsed = focus.focused_seconds + (focus.status === "running" ? tick : 0);
  const target = focus.work_minutes * 60;
  const remaining = Math.max(0, target - elapsed);
  const pct = Math.min(1, elapsed / target);
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const r = 44;
  const c = 2 * Math.PI * r;
  return (
    <Section title={`Focus: ${focus.title}`}>
      <div className="flex items-center gap-5 rounded-2xl bg-card p-4 ring-1 ring-line">
        <svg viewBox="0 0 100 100" className="h-24 w-24 shrink-0" role="img" aria-label={`${mm}:${ss} left`}>
          <circle cx="50" cy="50" r={r} fill="none" stroke="#e3e9ee" strokeWidth="8" />
          <circle cx="50" cy="50" r={r} fill="none" stroke={catColor(focus.category)} strokeWidth="8" strokeLinecap="round"
            strokeDasharray={c} strokeDashoffset={c * (1 - pct)} transform="rotate(-90 50 50)" />
          <text x="50" y="56" textAnchor="middle" className="font-display" fontSize="20" fontWeight="600" fill="var(--color-ink)">
            {remaining === 0 ? "Break" : `${mm}:${ss}`}
          </text>
        </svg>
        <div className="flex flex-wrap gap-2">
          {focus.status === "running" ? (
            <Button onClick={() => act(() => api.post(`/api/focus/${focus.id}`, { action: "pause" }))}>Pause</Button>
          ) : (
            <Button onClick={() => act(() => api.post(`/api/focus/${focus.id}`, { action: "resume" }))}>Resume</Button>
          )}
          <Button kind="primary" onClick={() => act(() => api.post(`/api/focus/${focus.id}`, { action: "complete" }))}>
            Finish and log {Math.max(1, Math.floor(elapsed / 60))} min
          </Button>
          <Button kind="danger" onClick={() => act(() => api.post(`/api/focus/${focus.id}`, { action: "stop" }))}>Stop</Button>
        </div>
      </div>
    </Section>
  );
}

function HealthPanel({ day }: { day: Day }) {
  const h = day.health;
  if ("whoop" in h) {
    return (
      <Section title="Body">
        <Empty>
          {h.whoop === "not connected" ? "WHOOP isn't connected. Send /whoop to Mami on Telegram to connect it." : h.whoop}
        </Empty>
      </Section>
    );
  }
  const rec = h.recovery_score;
  const recColor = rec == null ? "var(--color-other)" : rec >= 67 ? "#2a9d74" : rec >= 34 ? "#d99a2b" : "#c2493d";
  const stats: [string, string][] = [
    ["Sleep", h.sleep_hours != null ? `${h.sleep_hours}h` : "–"],
    ["HRV", h.hrv_ms != null ? `${Math.round(h.hrv_ms)} ms` : "–"],
    ["Strain", h.day_strain != null ? h.day_strain.toFixed(1) : "–"],
    ["Steps", h.steps_so_far != null ? h.steps_so_far.toLocaleString("en-GB") : "–"],
  ];
  return (
    <Section title="Body" action={<span className="text-xs text-soft">From WHOOP</span>}>
      <div className="flex items-center gap-4 rounded-2xl bg-card p-4 ring-1 ring-line">
        <div className="flex h-16 w-16 shrink-0 flex-col items-center justify-center rounded-full ring-4" style={{ ["--tw-ring-color" as string]: recColor }}>
          <span className="font-display text-xl font-semibold">{rec != null ? `${Math.round(rec)}%` : "–"}</span>
        </div>
        <dl className="grid flex-1 grid-cols-2 gap-x-4 gap-y-1 text-sm">
          {stats.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2">
              <dt className="text-soft">{k}</dt>
              <dd className="font-display font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Section>
  );
}
