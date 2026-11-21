"use client";

import { useState, type FormEvent } from "react";

import { SignInGate } from "@/components/SignInGate";
import { ingest, type IngestResult } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useMe } from "@/lib/useMe";

const TYPES = ["contract", "correspondence", "case_file", "policy", "legislation", "case_law"];

export default function UploadPage() {
  return (
    <SignInGate>
      <Upload />
    </SignInGate>
  );
}

function Upload() {
  const { config, getToken } = useAuth();
  const { me } = useMe();
  const [uploaded, setUploaded] = useState<number | null>(null);
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (me && !me.can_ingest) {
    return <p className="py-12">Only the Admin role can add documents. Ask an administrator.</p>;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!config) return;
    const form = new FormData(event.currentTarget);
    form.set("privileged", form.get("privileged") ? "true" : "false");
    setError(null);
    setResult(null);
    setUploaded(0);
    try {
      const token = await getToken();
      const done = await ingest(config.apiUrl, token, form, (fraction) => {
        setUploaded(fraction);
        if (fraction >= 1) setProcessing(true);
      });
      setResult(done);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProcessing(false);
      setUploaded(null);
    }
  }

  const busy = uploaded !== null;
  const percent = Math.round((uploaded ?? 0) * 100);
  return (
    <section className="rounded-lg bg-surface p-6">
      <h1 className="mb-4 font-serif text-3xl text-ink">Add a document</h1>
      <form onSubmit={submit} className="grid gap-4">
        <label className="grid gap-1 text-sm">
          File (PDF, Word, image or text)
          <input name="file" type="file" required className="text-sm" />
        </label>
        <label className="grid gap-1 text-sm">
          <span>
            Matter number, or <code>public</code> for legislation and published rulings
          </span>
          <input name="matter" required placeholder="BM/2026/H-0412" className="rounded bg-paper-soft px-3 py-2" />
        </label>
        <label className="grid gap-1 text-sm">
          Title
          <input name="title" className="rounded bg-paper-soft px-3 py-2" />
        </label>
        <label className="grid gap-1 text-sm">
          Type
          <select name="document_type" className="rounded bg-paper-soft px-3 py-2">
            {TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input name="privileged" type="checkbox" /> Legal professional privilege (only lawyers see it)
        </label>
        <button
          type="submit"
          disabled={busy}
          className="justify-self-start rounded bg-ember-deep px-5 py-2 text-surface hover:bg-ember disabled:opacity-50"
        >
          Upload
        </button>
      </form>

      {busy && (
        <div className="mt-6" role="status">
          <div className="h-2 overflow-hidden rounded bg-inset">
            <div className="h-full bg-ember transition-all" style={{ width: `${percent}%` }} />
          </div>
          <p className="mt-2 text-sm text-ink-muted">
            {processing
              ? "Uploaded. Reading the document, splitting it into passages and indexing them (up to a minute)…"
              : `Uploading… ${percent}%`}
          </p>
        </div>
      )}
      {result && (
        <p className="mt-6 rounded bg-sage px-3 py-2 text-sm text-ink">
          Indexed: {result.page_count} page(s), {result.chunk_count} passages, {result.entities_found} entities. It
          can be found in search now.
        </p>
      )}
      {error && <p className="mt-6 text-sm text-ember-deep">Upload failed: {error}</p>}
    </section>
  );
}
