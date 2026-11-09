"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Login } from "@/components/Login";
import { Today } from "@/components/Today";
import { Chat } from "@/components/Chat";
import { Goals, Me, Money, Tasks, Week } from "@/components/Pages";

type MeData = {
  today: string;
  name: string;
  local?: boolean;
  settings: { quiet_hours: string; coaching_style: string };
};

const ICONS: Record<string, React.ReactNode> = {
  chat: <path d="M5 18.5V6.5A1.5 1.5 0 0 1 6.5 5h11A1.5 1.5 0 0 1 19 6.5v8a1.5 1.5 0 0 1-1.5 1.5H9l-4 2.5z" />,
  today: <path d="M4 7h16M4 12h10M4 17h7" />,
  week: <path d="M5 19V11M10 19V6M15 19v-9M20 19V9" />,
  tasks: <path d="M4 7l2 2 3-3M4 16l2 2 3-3M12 8h8M12 17h8" />,
  money: <path d="M12 3v18M16.5 7.5c-.8-1.2-2.4-2-4.5-2-2.6 0-4 1.3-4 3s1.6 2.5 4 3 4 1.4 4 3.2-1.6 3-4.2 3c-2.1 0-3.7-.8-4.6-2.2" />,
  goals: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="0.8" />
    </>
  ),
  me: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c1.2-3.5 3.8-5 7-5s5.8 1.5 7 5" />
    </>
  ),
};

const TABS = [
  ["chat", "Chat"],
  ["today", "Today"],
  ["week", "Week"],
  ["tasks", "Tasks"],
  ["money", "Money"],
  ["goals", "Goals"],
  ["me", "Me"],
] as const;
type Tab = (typeof TABS)[number][0];

export default function Home() {
  const [me, setMe] = useState<MeData | null>(null);
  const [state, setState] = useState<"loading" | "login" | "ready" | "error">("loading");
  const [tab, setTab] = useState<Tab>("chat");

  const load = useCallback(async () => {
    try {
      setMe(await api.get<MeData>("/api/me"));
      setState("ready");
    } catch (e) {
      setState(e instanceof ApiError && e.status === 401 ? "login" : "error");
    }
  }, []);

  useEffect(() => {
    load();
    const fromHash = window.location.hash.slice(1) as Tab;
    if (TABS.some(([t]) => t === fromHash)) setTab(fromHash);
    const out = () => setState("login");
    window.addEventListener("dm:logged-out", out);
    return () => window.removeEventListener("dm:logged-out", out);
  }, [load]);

  function go(t: Tab) {
    setTab(t);
    history.replaceState(null, "", `#${t}`);
    window.scrollTo({ top: 0 });
  }

  if (state === "loading") return <p className="p-8 font-display text-lg text-soft">Mami is getting your day…</p>;
  if (state === "login") return <Login onDone={load} />;
  if (state === "error" || !me)
    return (
      <main className="p-8">
        <p className="mb-4">Can't reach the server. Check your connection, then try again.</p>
        <button onClick={load} className="rounded-full bg-ink px-4 py-2 font-semibold text-white">Try again</button>
      </main>
    );

  return (
    <div className="mx-auto max-w-6xl px-5 pb-28 pt-5 sm:px-8 lg:pb-12">
      <div className="mb-6 flex items-center justify-between lg:mb-8">
        <span className="flex items-center gap-2.5">
          <span className="sky h-7 w-1.5 rounded-full" aria-hidden />
          <span className="font-display text-2xl font-semibold tracking-tight">Mami</span>
        </span>
        <nav aria-label="Sections" className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-mist/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:static lg:border-0 lg:bg-transparent lg:pb-0">
          <ul className="mx-auto flex max-w-lg justify-around lg:max-w-none lg:gap-1">
            {TABS.map(([t, label]) => {
              const active = tab === t;
              return (
                <li key={t}>
                  <button
                    onClick={() => go(t)}
                    aria-current={active ? "page" : undefined}
                    className={`flex min-h-14 flex-col items-center justify-center gap-0.5 px-2 text-[0.7rem] font-semibold lg:min-h-10 lg:flex-row lg:gap-2 lg:rounded-full lg:px-4 lg:text-sm ${
                      active ? "text-accent lg:bg-card lg:text-ink lg:shadow-[0_1px_0_var(--color-line),0_0_0_1px_var(--color-line)]" : "text-soft hover:text-ink"
                    }`}
                  >
                    <svg viewBox="0 0 24 24" className="h-6 w-6 lg:h-[18px] lg:w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      {ICONS[t]}
                    </svg>
                    {label}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
      <main>
        {tab === "chat" && <Chat name={me.name} />}
        {tab === "today" && <Today today={me.today} name={me.name} />}
        {tab === "week" && <Week />}
        {tab === "tasks" && <Tasks />}
        {tab === "money" && <Money />}
        {tab === "goals" && <Goals />}
        {tab === "me" && (
          <Me
            settings={me.settings}
            onLogout={me.local ? undefined : async () => {
              await api.post("/api/auth/logout").catch(() => {});
              setState("login");
            }}
          />
        )}
      </main>
    </div>
  );
}
