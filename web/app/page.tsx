"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Login } from "@/components/Login";
import { Today } from "@/components/Today";
import { Goals, Me, Money, Tasks, Week } from "@/components/Pages";

type MeData = { today: string; settings: { quiet_hours: string; coaching_style: string } };
const TABS = [
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
  const [tab, setTab] = useState<Tab>("today");

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

  if (state === "loading") return <p className="p-8 text-soft">Loading your day…</p>;
  if (state === "login") return <Login onDone={load} />;
  if (state === "error" || !me)
    return (
      <main className="p-8">
        <p className="mb-4">Can't reach the server. Check your connection, then try again.</p>
        <button onClick={load} className="rounded-full bg-ink px-4 py-2 font-bold text-white">Try again</button>
      </main>
    );

  return (
    <div className="mx-auto max-w-6xl px-4 pb-28 pt-6 sm:px-8 lg:pb-10">
      <nav aria-label="Sections" className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-mist/95 backdrop-blur lg:static lg:mb-8 lg:border-0 lg:bg-transparent">
        <ul className="mx-auto flex max-w-6xl justify-around lg:justify-start lg:gap-2">
          {TABS.map(([t, label]) => (
            <li key={t}>
              <button
                onClick={() => go(t)}
                aria-current={tab === t ? "page" : undefined}
                className={`min-h-12 px-3 text-sm font-bold lg:rounded-full lg:px-4 ${
                  tab === t ? "text-ink lg:bg-ink lg:text-white" : "text-soft"
                }`}
              >
                {label}
                {tab === t && <span className="mx-auto mt-1 block h-1 w-5 rounded-full bg-ink lg:hidden" aria-hidden />}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <main>
        {tab === "today" && <Today today={me.today} />}
        {tab === "week" && <Week />}
        {tab === "tasks" && <Tasks />}
        {tab === "money" && <Money />}
        {tab === "goals" && <Goals />}
        {tab === "me" && (
          <Me
            settings={me.settings}
            onLogout={async () => {
              await api.post("/api/auth/logout").catch(() => {});
              setState("login");
            }}
          />
        )}
      </main>
    </div>
  );
}
