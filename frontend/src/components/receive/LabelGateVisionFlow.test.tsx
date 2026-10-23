import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReceivePage } from "./index";
import type { ConfirmationData, Order } from "./types";

const mocks = vi.hoisted(() => ({
  confirmBooking: vi.fn(),
  scanLabel: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: mocks,
  ApiError: class ApiError extends Error {
    status: number;
    detail: unknown;
    constructor(status: number, detail: unknown, message: string) {
      super(message);
      this.status = status;
      this.detail = detail;
    }
  },
}));

vi.mock("@/App", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/celebrate", () => ({ fireCompletion: vi.fn() }));

const order: Order = {
  id: 1,
  reference: "ADV-1",
  status: "active",
  channel: "advice",
  pick_method: "vision",
  created_at: "2026-09-21T09:00:00Z",
  customer_name: "Jan Posma",
  total_boxes: 0,
  booked_boxes: 0,
  total_bottles: 1,
  booked_bottles: 0,
  total_items: 1,
  booked_items: 0,
  lines: [],
};

const confirmation: ConfirmationData = {
  needs_confirmation: true,
  confirmation_token: "tok-1",
  order_id: 1,
  order_line_id: 10,
  order_reference: "ADV-1",
  sku_code: "GIOV-ROO",
  sku_name: "Giovanni Rosso",
  confidence: 0.95,
  klant: "Jan Posma",
  rolcontainer: "KLANT JAN POSMA",
  scan_image_url: "https://example.test/scan.jpg",
  reference_image_url: "https://example.test/ref.jpg",
};

vi.mock("./OrderSelectStep", () => ({
  OrderSelectStep: ({ onSelect }: { onSelect: (o: Order) => void }) => (
    <button type="button" onClick={() => onSelect(order)}>
      Kies order
    </button>
  ),
}));

vi.mock("./ScanStep", () => ({
  ScanStep: ({ onBooked }: { onBooked: (c: ConfirmationData) => void }) => (
    <button type="button" onClick={() => onBooked(confirmation)}>
      Scan fles
    </button>
  ),
}));

beforeEach(() => {
  mocks.confirmBooking.mockReset();
  mocks.scanLabel.mockReset();
});

afterEach(() => cleanup());

describe("vision flow shipping-label gate", () => {
  it("sends a completed advice-app order through the label gate before it can finish", async () => {
    const user = userEvent.setup();
    mocks.confirmBooking.mockResolvedValue({
      id: 99,
      order_id: 1,
      order_line_id: 10,
      order_reference: "ADV-1",
      sku_code: "GIOV-ROO",
      sku_name: "Giovanni Rosso",
      klant: "Jan Posma",
      rolcontainer: "KLANT JAN POSMA",
      booked_quantity: 1,
      remaining_quantity: 0,
      order_completed: true,
      needs_label: true,
    });
    mocks.scanLabel.mockResolvedValue({
      order_id: 1,
      status: "shipped",
      reference: "ADV-1",
      parcels_total: 1,
      parcels_scanned: 1,
    });

    render(<ReceivePage />);
    await user.click(screen.getByRole("button", { name: "Kies order" }));
    await user.click(screen.getByRole("button", { name: "Scan fles" }));
    await user.click(await screen.findByRole("button", { name: "Ja, dit klopt" }));

    // The order is done, but it must not offer "terug naar orders" yet.
    const labelButton = await screen.findByRole("button", {
      name: "Verzendlabel scannen →",
    });
    expect(screen.queryByRole("button", { name: "Terug naar orders" })).toBeNull();

    await user.click(labelButton);

    expect(
      await screen.findByText("Order compleet — inpakken en scan verzendlabel"),
    ).toBeTruthy();

    await user.type(
      screen.getByPlaceholderText("Scan het verzendlabel…"),
      "3SIJVT018280390",
    );
    await user.click(screen.getByRole("button", { name: "Verzenden" }));

    await waitFor(() => expect(mocks.scanLabel).toHaveBeenCalledWith(1, "3SIJVT018280390"));
    expect(await screen.findByText("Verzendklaar")).toBeTruthy();
  });

  it("lets a completed manual (b2b) order finish straight onto the rolcontainer", async () => {
    const user = userEvent.setup();
    mocks.confirmBooking.mockResolvedValue({
      id: 99,
      order_id: 1,
      order_line_id: 10,
      order_reference: "ADV-1",
      sku_code: "GIOV-ROO",
      sku_name: "Giovanni Rosso",
      klant: "Jan Posma",
      rolcontainer: "KLANT JAN POSMA",
      booked_quantity: 1,
      remaining_quantity: 0,
      order_completed: true,
      needs_label: false,
    });

    render(<ReceivePage />);
    await user.click(screen.getByRole("button", { name: "Kies order" }));
    await user.click(screen.getByRole("button", { name: "Scan fles" }));
    await user.click(await screen.findByRole("button", { name: "Ja, dit klopt" }));

    expect(
      await screen.findByRole("button", { name: "Terug naar orders" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Verzendlabel scannen →" }),
    ).toBeNull();
  });
});
