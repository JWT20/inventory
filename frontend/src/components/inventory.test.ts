import { describe, expect, it } from "vitest";

import { parseTransferQuantity } from "./inventory";

describe("parseTransferQuantity", () => {
  it("accepts positive whole numbers without truncating number syntax", () => {
    expect(parseTransferQuantity("6")).toBe(6);
    expect(parseTransferQuantity("1e2")).toBe(100);
  });

  it("rejects fractional, empty, zero, and negative quantities", () => {
    expect(parseTransferQuantity("1.5")).toBeNull();
    expect(parseTransferQuantity("")).toBeNull();
    expect(parseTransferQuantity("0")).toBeNull();
    expect(parseTransferQuantity("-1")).toBeNull();
  });
});
