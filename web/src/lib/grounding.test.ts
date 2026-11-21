import { describe, expect, it } from "vitest";

import { groundingOf } from "./grounding";

describe("groundingOf", () => {
  it("labels every grounding value the API sends", () => {
    for (const value of ["documents", "not_grounded", "unverified", "no_documents", "blocked"]) {
      expect(groundingOf(value)?.label).toBeTruthy();
    }
  });

  it("warns when the answer isn't backed by its sources", () => {
    expect(groundingOf("not_grounded")?.tone).toBe("warn");
    expect(groundingOf("unverified")?.tone).toBe("warn");
    expect(groundingOf("documents")?.tone).toBe("ok");
  });

  it("shows nothing for an unknown value", () => {
    expect(groundingOf("something_new")).toBeNull();
    expect(groundingOf(null)).toBeNull();
  });
});
