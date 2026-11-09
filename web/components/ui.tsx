"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";

const CATEGORY_COLORS: Record<string, string> = {
  study: "var(--color-study)",
  exercise: "var(--color-exercise)",
  gym: "var(--color-exercise)",
  fitness: "var(--color-exercise)",
  work: "var(--color-work)",
  commute: "var(--color-commute)",
  transport: "var(--color-commute)",
  rest: "var(--color-rest)",
  meditation: "var(--color-rest)",
  meal: "var(--color-meal)",
  food: "var(--color-meal)",
  groceries: "var(--color-meal)",
  social: "var(--color-social)",
  entertainment: "var(--color-social)",
  project: "var(--color-project)",
};

export function catColor(category: string | null | undefined): string {
  return CATEGORY_COLORS[(category || "").toLowerCase()] || "var(--color-other)";
}

export function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-7">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="font-display text-lg font-semibold tracking-tight">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  kind = "quiet",
  disabled,
  type = "button",
  className = "",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  kind?: "primary" | "quiet" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    primary: "bg-ink text-white hover:bg-[#2c3b4b]",
    quiet: "bg-white text-ink ring-1 ring-line hover:bg-[#f6f8fa]",
    danger: "bg-white text-[#b3261e] ring-1 ring-line hover:bg-[#fff4f3]",
  }[kind];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`min-h-10 rounded-full px-4 text-sm font-bold disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-2xl border border-dashed border-line px-4 py-5 text-sm text-soft">{children}</p>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p role="alert" className="mb-4 rounded-xl bg-[#fff4f3] px-4 py-3 text-sm text-[#8c1d18]">
      {error}
    </p>
  );
}

/** Load data, expose reload, surface errors (401 bubbles up to log the user out). */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await load());
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        window.dispatchEvent(new Event("dm:logged-out"));
      }
      setError(e instanceof Error ? e.message : "Couldn't load this.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setError };
}

export function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((100 * value) / max)) : 0;
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full bg-[#e3e9ee]" aria-hidden>
      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function minutesLabel(m: number): string {
  const h = Math.floor(m / 60);
  const r = Math.round(m % 60);
  if (h === 0) return `${r} min`;
  return r ? `${h}h ${r}m` : `${h}h`;
}

export const STATUS_LABEL: Record<string, string> = {
  planned: "Planned",
  in_progress: "In progress",
  completed: "Done",
  missed: "Skipped",
  cancelled: "Cancelled",
  unknown: "Not reported",
};
