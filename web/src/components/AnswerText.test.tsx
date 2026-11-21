import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Citation } from "@/lib/citations";

import { AnswerText } from "./AnswerText";

const article: Citation = {
  index: 9,
  document_id: "BWBR0005290 art. 271",
  document_title: "Artikel 7:271 BW",
  chunk_content: "1. In afwijking van artikel 228 lid 1 ...",
  page_number: null,
  relevance_score: 1,
  url: "https://wetten.overheid.nl/jci1.0:c:BWBR0005290&artikel=271",
};

describe("AnswerText", () => {
  it("turns a cited source into a button that opens it", () => {
    const onCite = vi.fn();
    render(<AnswerText text="Opzeggen kan [9]." citations={[article]} onCite={onCite} />);
    fireEvent.click(screen.getByRole("button", { name: "Source 9: Artikel 7:271 BW" }));
    expect(onCite).toHaveBeenCalledWith(article);
  });

  it("keeps a marker without a shown source as text", () => {
    render(<AnswerText text="Zie [3]." citations={[article]} onCite={() => {}} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("[3]")).toBeTruthy();
  });
});

describe("AnswerText Markdown", () => {
  it("shows headings, bold and rules, with citations inside them", () => {
    const { container } = render(
      <AnswerText text={"## Opzegging [9]\n**Termijn:** één maand\n---\n<b>geen html</b>"} citations={[article]} onCite={() => {}} />,
    );
    expect(container.querySelector("h3")?.textContent).toBe("Opzegging 9");
    expect(container.querySelector("strong")?.textContent).toBe("Termijn:");
    expect(container.querySelector("hr")).toBeTruthy();
    expect(container.querySelector("b")).toBeNull(); // the model's HTML stays text
    expect(screen.getByText("<b>geen html</b>")).toBeTruthy();
  });
});
