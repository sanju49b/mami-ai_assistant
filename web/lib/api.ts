// Tiny typed client for the FastAPI backend (same origin, cookie session).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
};

// ---- types mirrored from app/views.py ----

export type Status = "planned" | "in_progress" | "completed" | "missed" | "cancelled" | "unknown";

export type Block = {
  id: string;
  title: string;
  category: string;
  start: string;
  end: string;
  starts_at: string;
  ends_at: string;
  status: Status;
  important: boolean;
  checklist: { text: string; done: boolean }[];
  version: number;
};

export type Activity = {
  id: string;
  title: string;
  category: string;
  status: Status;
  minutes: number | null;
  start: string | null;
  end: string | null;
  source: string;
  notes: string | null;
};

export type Expense = {
  id: string;
  amount: string;
  amount_pence: number;
  category: string;
  merchant: string | null;
  note: string | null;
  date: string;
  status: "confirmed" | "estimated";
  worth_it: number | null;
};

export type Task = {
  id: string;
  title: string;
  kind: string;
  status: "open" | "done" | "cancelled";
  due_date: string | null;
  due_time: string | null;
  important: boolean;
  version: number;
};

export type Approval = { id: string; action: string; summary: string; created_at: string };

export type Focus = {
  id: string;
  title: string;
  category: string;
  status: "running" | "paused" | "completed" | "stopped";
  work_minutes: number;
  break_minutes: number;
  started_at: string;
  focused_seconds: number;
};

export type Health =
  | { whoop: string }
  | {
      source: string;
      recovery_score: number | null;
      hrv_ms: number | null;
      resting_hr: number | null;
      sleep_performance: number | null;
      sleep_hours: number | null;
      day_strain: number | null;
      steps_so_far: number | null;
      workouts: { sport: string; strain: number | null }[];
    };

export type Day = {
  date: string;
  weekday: string;
  plan: { version_no: number; versions: number; note: string | null } | null;
  blocks: Block[];
  activities: Activity[];
  expenses: Expense[];
  spent_total: string;
  tasks: Task[];
  health: Health;
  review: { summary: string | null } | null;
  approvals: Approval[];
  focus: Focus | null;
};

export type Goal = {
  id: string;
  kind: string;
  title: string;
  category: string | null;
  metric: string | null;
  target: number | null;
  period: string | null;
  deadline: string | null;
  this_week?: { actual: number | null; target: number | null; percent: number | null };
};

export type Memory = {
  id: string;
  content: string;
  kind: string;
  status: "confirmed" | "observed";
  version: number;
};
