import { groundingOf } from "@/lib/grounding";

const TONES = {
  ok: "bg-sage text-ink",
  warn: "bg-peach text-ink",
  info: "bg-lilac text-ink",
};

export function GroundingBadge({ grounding }: { grounding: string | null | undefined }) {
  const info = groundingOf(grounding);
  if (!info) return null;
  return (
    <p className={`mt-3 inline-block rounded px-2 py-1 text-xs ${TONES[info.tone]}`} title={info.note} data-testid="grounding">
      <span className="font-medium">{info.label}</span>
      {info.tone !== "ok" && <span className="block">{info.note}</span>}
    </p>
  );
}
