"use client";

import Link from "next/link";

import { useAuth } from "@/lib/auth";
import { useMe } from "@/lib/useMe";

export function Header() {
  const { config, account, signIn, signOut } = useAuth();
  const { me } = useMe();
  return (
    <header className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-4 py-6">
      <Link href="/" className="font-serif text-2xl font-semibold text-ink">
        LegalMind
      </Link>
      <nav className="flex items-center gap-4 text-sm">
        <Link href="/" className="text-ink-body hover:text-ember-deep">
          Chat
        </Link>
        {me?.can_ingest && (
          <Link href="/upload/" className="text-ink-body hover:text-ember-deep">
            Upload
          </Link>
        )}
        {config?.auth ? (
          account ? (
            <span className="flex items-center gap-3">
              <span className="text-ink-muted" title={me?.roles.join(", ")}>
                {account.name ?? account.username}
              </span>
              <button onClick={signOut} className="rounded border border-rule px-3 py-1 hover:border-ember-deep">
                Sign out
              </button>
            </span>
          ) : (
            <button onClick={signIn} className="rounded bg-ember-deep px-3 py-1 text-surface hover:bg-ember">
              Sign in
            </button>
          )
        ) : (
          config && <span className="rounded bg-peach px-2 py-0.5 text-xs text-ink">sign-in off (development)</span>
        )}
      </nav>
    </header>
  );
}
