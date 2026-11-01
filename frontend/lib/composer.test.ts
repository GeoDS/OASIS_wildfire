import { describe, expect, it } from "vitest";

import { withPickedOption } from "./composer";

const FETCH = ["Fetch it", "Answer without it"];

describe("withPickedOption", () => {
  it("starts an empty reply with the pick", () => {
    expect(withPickedOption("", FETCH, "Fetch it")).toBe("Fetch it");
  });

  it("replaces an earlier pick from the same question", () => {
    expect(withPickedOption("Fetch it", FETCH, "Answer without it")).toBe("Answer without it");
    expect(withPickedOption("Answer without it", FETCH, "Fetch it")).toBe("Fetch it");
  });

  it("keeps answers to other questions", () => {
    expect(withPickedOption("Official perimeters; Fetch it", FETCH, "Answer without it")).toBe(
      "Official perimeters; Answer without it",
    );
  });

  it("does not repeat a pick made twice", () => {
    expect(withPickedOption("Fetch it", FETCH, "Fetch it")).toBe("Fetch it");
  });
});
