"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Approval } from "@/lib/api";
import { Button } from "@/components/ui";

type ChatMessage = { id: string; role: "user" | "assistant"; text: string; at: string | null };
type ChatData = { messages: ChatMessage[]; approvals: Approval[] };

const EXAMPLES = [
  "Plan tomorrow: DSA 7-8, commute, office 9-5, gym 6-7:30",
  "Did 45 min DSA, £4 coffee, skipped gym",
  "How am I doing this week?",
];

// Turn plain URLs in Mami's messages into links (e.g. the WHOOP connect link).
function Linked({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s]+)/g);
  return (
    <>
      {parts.map((p, i) =>
        /^https?:\/\//.test(p) ? (
          <a key={i} href={p} target="_blank" rel="noreferrer" className="break-all underline">
            {p}
          </a>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function timeLabel(at: string | null): string {
  if (!at) return "";
  const d = new Date(at);
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString(undefined, today ? { hour: "2-digit", minute: "2-digit" } : { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

export function Chat({ name }: { name: string }) {
  const [data, setData] = useState<ChatData | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api.get<ChatData>("/api/chat"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Can't reach Mami.");
    }
  }, []);

  useEffect(() => {
    refresh();
    // Mami also writes on her own (morning brief, check-ins, reminders).
    const t = setInterval(() => {
      if (!document.hidden) refresh();
    }, 30000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [data?.messages.length, pending]);

  async function send(text: string) {
    const clean = text.trim();
    if (!clean || pending) return;
    setPending(clean);
    setDraft("");
    setError(null);
    try {
      setData(await api.post<ChatData>("/api/chat", { text: clean }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Can't reach Mami.");
      await refresh(); // your message is saved even if the reply failed
    } finally {
      setPending(null);
      box.current?.focus();
    }
  }

  async function decide(a: Approval, decision: "approve" | "reject") {
    setError(null);
    try {
      await api.post(`/api/approvals/${a.id}`, { decision });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't work.");
    }
    await refresh();
  }

  const messages = data?.messages ?? [];

  return (
    <div className="mx-auto flex max-w-2xl flex-col">
      {messages.length === 0 && !pending && data && (
        <div className="mb-6 rounded-3xl bg-card p-6 ring-1 ring-line">
          <p className="font-display text-2xl font-semibold tracking-tight">Hi {name}, I'm Mami.</p>
          <p className="mt-2 text-soft">
            Tell me what you plan to do and what you did, in your own words. I'll keep your day, your
            spending and your goals, and nudge you along. Try one:
          </p>
          <div className="mt-4 flex flex-col gap-2">
            {EXAMPLES.map((e) => (
              <button key={e} onClick={() => send(e)} className="rounded-2xl bg-mist px-4 py-3 text-left text-sm ring-1 ring-line hover:bg-[#f0eae2]">
                {e}
              </button>
            ))}
          </div>
        </div>
      )}

      <ol className="flex flex-col gap-3" aria-live="polite">
        {messages.map((m) => (
          <li key={m.id} className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}>
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-3xl px-4 py-3 leading-relaxed ${
                m.role === "user" ? "rounded-br-lg bg-ink text-white" : "rounded-bl-lg bg-card ring-1 ring-line"
              }`}
            >
              <Linked text={m.text} />
            </div>
            <span className="mt-1 px-2 text-xs text-soft">{timeLabel(m.at)}</span>
          </li>
        ))}
        {pending && (
          <>
            <li className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg bg-ink px-4 py-3 text-white">{pending}</div>
            </li>
            <li className="flex justify-start">
              <div className="rounded-3xl rounded-bl-lg bg-card px-4 py-3 text-soft ring-1 ring-line">Mami is thinking…</div>
            </li>
          </>
        )}
      </ol>

      {data && data.approvals.length > 0 && (
        <div className="mt-5 flex flex-col gap-3">
          {data.approvals.map((a) => (
            <div key={a.id} className="rounded-3xl bg-card p-4 ring-1 ring-[color:var(--color-accent)]/40">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-accent">Mami suggests</p>
              <p className="mb-3 whitespace-pre-wrap">{a.summary}</p>
              <div className="flex gap-2">
                <Button kind="primary" onClick={() => decide(a, "approve")}>Approve</Button>
                <Button onClick={() => decide(a, "reject")}>Reject</Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <p className="mt-4 rounded-2xl bg-[#fff4f3] px-4 py-3 text-sm text-[#b3261e] ring-1 ring-[#f3d3cf]">{error}</p>}
      <div ref={bottom} className="h-4" />

      <form
        className="sticky bottom-20 mt-2 flex items-end gap-2 rounded-3xl bg-card p-2 ring-1 ring-line focus-within:ring-2 focus-within:ring-accent/50 lg:bottom-4"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <textarea
          ref={box}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(draft);
            }
          }}
          rows={1}
          placeholder="Message Mami…"
          aria-label="Message Mami"
          className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-3 py-2 outline-none focus-visible:outline-none"
          autoFocus
        />
        <Button kind="primary" type="submit" disabled={!draft.trim() || !!pending}>
          Send
        </Button>
      </form>
    </div>
  );
}
