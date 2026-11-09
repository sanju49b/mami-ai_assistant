"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button, ErrorNote } from "./ui";

export function Login({ onDone }: { onDone: () => void }) {
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/request-code");
      setSent(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send a code.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/verify", { code });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code didn't work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6">
      <div className="sky mb-8 h-24 w-3 rounded-full" aria-hidden />
      <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight">
        Your day, laid out.
      </h1>
      <p className="mt-3 text-soft">
        Log in with a code sent to your Telegram. No password needed.
      </p>
      <div className="mt-8">
        <ErrorNote error={error} />
        {!sent ? (
          <Button kind="primary" onClick={send} disabled={busy} className="w-full">
            Send me a code
          </Button>
        ) : (
          <form onSubmit={verify} className="flex flex-col gap-3">
            <label htmlFor="code" className="text-sm font-bold">
              6-digit code from Telegram
            </label>
            <input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="rounded-2xl bg-white px-4 py-3 text-center font-display text-3xl tracking-[0.4em] ring-1 ring-line"
            />
            <Button kind="primary" type="submit" disabled={busy || code.length !== 6}>
              Log in
            </Button>
            <button type="button" onClick={send} className="text-sm text-soft underline">
              Send a new code
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
