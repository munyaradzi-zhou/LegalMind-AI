import { describe, expect, it } from "vitest";

import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("finds single, grouped and adjacent markers", () => {
    expect(splitCitations("De huur is € 1.185 [1]. Opzegging [2, 3][4].")).toEqual([
      { text: "De huur is € 1.185 " },
      { cite: 1 },
      { text: ". Opzegging " },
      { cite: 2 },
      { cite: 3 },
      { cite: 4 },
      { text: "." },
    ]);
  });

  it("leaves text without markers alone", () => {
    expect(splitCitations("Geen bronnen.")).toEqual([{ text: "Geen bronnen." }]);
  });

  it("doesn't take article numbers for citations", () => {
    expect(splitCitations("artikel 7:271 BW")).toEqual([{ text: "artikel 7:271 BW" }]);
  });
});
