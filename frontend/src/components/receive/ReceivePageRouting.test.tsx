import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReceivePage } from "./index";
import type { Order } from "./types";

function testOrder(pickMethod: "vision" | "barcode"): Order {
  return {
    id: pickMethod === "vision" ? 1 : 2,
    reference: pickMethod === "vision" ? "VISION-1" : "EAN-1",
    status: "active",
    channel: pickMethod === "vision" ? "manual" : "shopify",
    pick_method: pickMethod,
    customer_name: "Testklant",
    total_boxes: 1,
    booked_boxes: 0,
    total_bottles: 0,
    booked_bottles: 0,
    lines: [],
  };
}

vi.mock("./OrderSelectStep", () => ({
  OrderSelectStep: ({ onSelect }: { onSelect: (order: Order) => void }) => (
    <div>
      <button type="button" onClick={() => onSelect(testOrder("vision"))}>
        Kies vision-order
      </button>
      <button type="button" onClick={() => onSelect(testOrder("barcode"))}>
        Kies barcodeorder
      </button>
    </div>
  ),
}));

vi.mock("./ScanStep", () => ({
  ScanStep: () => <div>Normale dozen-scan</div>,
}));

vi.mock("./EanScanStep", () => ({
  EanScanStep: () => <div>Ordergebonden EAN-scan</div>,
}));

afterEach(() => cleanup());

describe("ReceivePage order routing", () => {
  it("keeps the normal vision box scan after removing scan without order", async () => {
    const user = userEvent.setup();
    render(<ReceivePage />);

    await user.click(screen.getByRole("button", { name: "Kies vision-order" }));

    expect(screen.getByText("Normale dozen-scan")).toBeTruthy();
  });

  it("keeps barcode orders on the existing EAN scan step", async () => {
    const user = userEvent.setup();
    render(<ReceivePage />);

    await user.click(screen.getByRole("button", { name: "Kies barcodeorder" }));

    expect(screen.getByText("Ordergebonden EAN-scan")).toBeTruthy();
  });
});
