import { describe, expect, it } from "vitest";

import {
  bookedQuantityLabel,
  bottleRemainderForBoxSku,
  resolveQuantityForUnit,
} from "./inbound";


describe("inbound quantity units", () => {
  it("counts pieces one-to-one for a bottle SKU", () => {
    expect(resolveQuantityForUnit(3, "pieces", true)).toBe(3);
    expect(resolveQuantityForUnit(8, "pieces", true)).toBe(8);
  });

  it("converts complete cases for a box SKU", () => {
    expect(resolveQuantityForUnit(6, "pieces", false)).toBe(1);
    expect(resolveQuantityForUnit(12, "pieces", false)).toBe(2);
  });

  it("detects bottles silently left over by the automatic box conversion", () => {
    expect(bottleRemainderForBoxSku({
      supplier_code: "AFS290021",
      description: "Rioja",
      quantity_boxes: 1,
      quantity: 8,
      quantity_unit: "pieces",
      confidence: 1,
      matched_sku_id: 1,
      matched_sku_code: "RIOJA",
      matched_sku_name: "Rioja",
      is_bottle: false,
    })).toBe(2);
  });
});

describe("booked inbound stock details", () => {
  it("labels boxes and bottles separately", () => {
    expect(bookedQuantityLabel({
      sku_id: 1,
      sku_code: "BOX-1",
      sku_name: "Rioja",
      quantity: 2,
      is_bottle: false,
    })).toBe("2 dozen");
    expect(bookedQuantityLabel({
      sku_id: 2,
      sku_code: "BOTTLE-1",
      sku_name: "Proeffles",
      quantity: 1,
      is_bottle: true,
    })).toBe("1 fles");
  });
});
