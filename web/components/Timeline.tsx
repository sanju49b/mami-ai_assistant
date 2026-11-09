"use client";

import { useEffect, useState } from "react";
import type { Block } from "@/lib/api";
import { catColor, STATUS_LABEL } from "./ui";

const PX_PER_MIN = 1.05;
const FIXED = new Set(["work", "commute"]);
const DAY_END_HOUR = 4;

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  const mins = h * 60 + m;
  // After midnight belongs to the same day (the day ends at 04:00).
  return h < DAY_END_HOUR ? mins + 24 * 60 : mins;
}

function nowMinutesLondon(): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
  return toMinutes(parts);
}

export function Timeline({
  blocks,
  isToday,
  selectedId,
  onSelect,
}: {
  blocks: Block[];
  isToday: boolean;
  selectedId: string | null;
  onSelect: (b: Block) => void;
}) {
  const [now, setNow] = useState<number>(() => nowMinutesLondon());
  useEffect(() => {
    const t = setInterval(() => setNow(nowMinutesLondon()), 60_000);
    return () => clearInterval(t);
  }, []);

  const starts = blocks.map((b) => toMinutes(b.start));
  const ends = blocks.map((b) => toMinutes(b.end));
  const first = Math.floor(Math.min(6 * 60, ...starts) / 60) * 60;
  const last = Math.ceil(Math.max(23 * 60, ...ends) / 60) * 60;
  const height = (last - first) * PX_PER_MIN;
  const hours: number[] = [];
  for (let m = first; m <= last; m += 60) hours.push(m);

  // The sky band shows the full 24h gradient, shifted so each hour gets its colour.
  const skyStyle = {
    backgroundSize: `100% ${24 * 60 * PX_PER_MIN}px`,
    backgroundPosition: `0 ${-((first % (24 * 60)) * PX_PER_MIN)}px`,
  };

  return (
    <div className="relative flex" style={{ height }} aria-label="Today's plan">
      <div className="relative w-12 shrink-0 text-right text-xs text-soft" aria-hidden>
        {hours.map((m) => (
          <span
            key={m}
            className="absolute right-3 -translate-y-1/2 font-display tabular-nums"
            style={{ top: (m - first) * PX_PER_MIN }}
          >
            {String(Math.floor(m / 60) % 24).padStart(2, "0")}:00
          </span>
        ))}
      </div>
      <div className="sky mr-3 w-2.5 shrink-0 rounded-full" style={skyStyle} aria-hidden />
      <ol className="relative flex-1">
        {hours.map((m) => (
          <li
            key={`l${m}`}
            aria-hidden
            className="pointer-events-none absolute inset-x-0 border-t border-dashed border-line"
            style={{ top: (m - first) * PX_PER_MIN }}
          />
        ))}
        {blocks.map((b, i) => {
          const top = (starts[i] - first) * PX_PER_MIN;
          const h = Math.max(30, (ends[i] - starts[i]) * PX_PER_MIN - 3);
          const color = catColor(b.category);
          const done = b.status === "completed";
          const off = b.status === "missed" || b.status === "cancelled";
          const unknown = b.status === "unknown";
          const fixed = FIXED.has(b.category) && !done;
          const ticked = b.checklist.filter((c) => c.done).length;
          return (
            <li key={b.id} className="absolute inset-x-0" style={{ top, height: h }}>
              <button
                onClick={() => onSelect(b)}
                aria-pressed={selectedId === b.id}
                className={`flex h-full w-full overflow-hidden rounded-2xl text-left transition-shadow ${
                  selectedId === b.id ? "shadow-[0_0_0_2px_var(--color-ink)]" : ""
                } ${unknown ? "border border-dashed border-soft/50" : ""} ${fixed ? "fixed-block" : ""}`}
                style={{
                  background: done
                    ? color
                    : `color-mix(in oklab, ${color} ${off ? 5 : fixed ? 8 : 15}%, var(--color-card))`,
                  color: done ? "white" : "var(--color-ink)",
                }}
              >
                <span className="w-1.5 shrink-0" style={{ background: color, opacity: off ? 0.35 : 1 }} />
                <span className="flex min-w-0 flex-1 flex-col justify-center px-3 py-1">
                  <span className={`truncate ${fixed ? "font-medium" : "font-semibold"} ${off ? "line-through opacity-60" : ""}`}>
                    {b.important && <span className="sr-only">Important: </span>}
                    {b.title}
                  </span>
                  {h > 44 && (
                    <span className={`truncate font-display text-xs ${done ? "text-white/85" : "text-soft"}`}>
                      {b.start}–{b.end}
                      {b.status !== "planned" && ` · ${STATUS_LABEL[b.status]}`}
                      {b.checklist.length > 0 && ` · ${ticked}/${b.checklist.length}`}
                    </span>
                  )}
                </span>
                {b.important && !done && (
                  <span className="mr-3 mt-3 h-2 w-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden />
                )}
              </button>
            </li>
          );
        })}
        {isToday && now >= first && now <= last && (
          <li
            aria-label="Now"
            className="pointer-events-none absolute inset-x-0 z-10 flex items-center"
            style={{ top: (now - first) * PX_PER_MIN }}
          >
            <span className="-ml-1.5 h-3 w-3 rounded-full bg-focus ring-2 ring-white" />
            <span className="h-0.5 flex-1 bg-focus" />
          </li>
        )}
      </ol>
    </div>
  );
}
