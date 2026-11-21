"use client";

import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";

/** Shows its children once the user may use the app (signed in, or sign-in off). */
export function SignInGate({ children }: { children: ReactNode }) {
  const { ready, config, account, signIn, error } = useAuth();
  if (!ready) return <p className="py-12 text-ink-muted">Loading…</p>;
  if (error) return <p className="py-12 text-ember-deep">Could not start: {error}</p>;
  if (config?.auth && !account) {
    return (
      <section className="rounded-lg bg-surface p-8 text-center">
        <h1 className="mb-2 font-serif text-3xl text-ink">Dutch legal research, with sources</h1>
        <p className="mb-6">
          Sign in with your work account. You see the documents of the matters you work on, and public law.
        </p>
        <button onClick={signIn} className="rounded bg-ember-deep px-5 py-2 text-surface hover:bg-ember">
          Sign in with Microsoft
        </button>
      </section>
    );
  }
  return <>{children}</>;
}
