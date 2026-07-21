import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { EanScanStep } from "./EanScanStep";
import { OrderSelectStep } from "./OrderSelectStep";
import type { Order } from "./types";

const mocks = vi.hoisted(() => ({
  listOrders: vi.fn(),
  openOrderByLabel: vi.fn(),
  getOrder: vi.fn(),
  scanLocation: vi.fn(),
  scanEan: vi.fn(),
  scanLabel: vi.fn(),
  undoScan: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: mocks,
  ApiError: class ApiError extends Error {
    constructor(_status: number, _detail: unknown, message: string) {
      super(message);
    }
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { role: "courier", is_platform_admin: false },
  }),
}));

vi.mock("@/App", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/celebrate", () => ({ fireCompletion: vi.fn() }));

vi.mock("./CameraBarcodeScanner", () => ({
  CameraBarcodeScanner: ({
    open,
    mode,
    onScan,
  }: {
    open: boolean;
    mode: "ean" | "label" | "location";
    onScan: (code: string) => void;
  }) => open ? (
    <button
      type="button"
      onClick={() =>
        onScan(
          mode === "ean"
            ? "8711111111111"
            : mode === "location"
              ? "LOC-1"
              : "V-LABEL-1",
        )
      }
    >
      camera-result-{mode}
    </button>
  ) : null,
}));

const baseLine = {
  id: 10,
  sku_id: 20,
  sku_code: "SOK-1",
  sku_name: "Testsok",
  delivery_day: "wednesday",
  customer_name: "Testklant",
  quantity: 2,
  booked_count: 0,
  is_bottle: false,
};

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: 1,
    reference: "ORD-1",
    status: "active",
    channel: "shopify",
    pick_method: "barcode",
    created_at: "2026-07-21T09:00:00Z",
    customer_name: "Testklant",
    total_boxes: 0,
    booked_boxes: 0,
    total_bottles: 0,
    booked_bottles: 0,
    total_items: 2,
    booked_items: 0,
    lines: [baseLine],
    ...overrides,
  };
}

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.listOrders.mockResolvedValue([]);
  mocks.scanLocation.mockResolvedValue({
    order_id: 1,
    location_code: "LOC-1",
    skus: [],
  });
  mocks.scanEan.mockResolvedValue({
    order_id: 1,
    order_line_id: 10,
    sku_id: 20,
    sku_code: "SOK-1",
    sku_name: "Testsok",
    klant: "Testklant",
    rolcontainer: "KLANT TESTKLANT",
    booked_quantity: 1,
    remaining_quantity: 1,
    order_completed: false,
    booking_id: 30,
  });
  mocks.scanLabel.mockResolvedValue({ order_id: 1, status: "shipped", reference: "1" });
});

afterEach(() => cleanup());

describe("camera scan integration", () => {
  it("keeps EAN scanning available without orders and removes the top scan bar", async () => {
    render(
      <OrderSelectStep
        onSelect={vi.fn()}
        onThisWeek={vi.fn()}
      />,
    );

    await screen.findByText(/Geen actieve orders in/);
    expect(screen.getByRole("button", { name: "EAN scannen" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("Scan Veloyd-label…")).toBeNull();
    expect(screen.queryByText("Scan zonder order")).toBeNull();
  });

  it("keeps EAN scanning available for a vision-only week", async () => {
    mocks.listOrders.mockResolvedValue([order({ pick_method: "vision" })]);

    render(
      <OrderSelectStep
        onSelect={vi.fn()}
        onThisWeek={vi.fn()}
      />,
    );

    await screen.findByText("ORD-1");
    expect(screen.getByRole("button", { name: "EAN scannen" })).toBeTruthy();
    expect(screen.getByText("Kies een order hieronder.")).toBeTruthy();
  });

  it("opens an order with a hardware scanner from the EAN label panel", async () => {
    const user = userEvent.setup();
    const selected = vi.fn();
    const foundOrder = order();
    mocks.openOrderByLabel.mockResolvedValue({ order_id: 1, tracking_code: "vlabel1" });
    mocks.getOrder.mockResolvedValue(foundOrder);

    render(
      <OrderSelectStep
        onSelect={selected}
        onThisWeek={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "EAN scannen" }));
    expect(screen.getByText("Scan het Veloyd-label van een order")).toBeTruthy();

    const input = screen.getByPlaceholderText("Scan Veloyd-label…");
    expect(document.activeElement).toBe(input);
    await user.type(input, "V-LABEL-1{Enter}");

    await waitFor(() =>
      expect(mocks.openOrderByLabel).toHaveBeenCalledWith("V-LABEL-1"),
    );
    expect(selected).toHaveBeenCalledWith(foundOrder);
  });

  it("opens an order with the camera from the EAN label panel", async () => {
    const user = userEvent.setup();
    const selected = vi.fn();
    const foundOrder = order();
    mocks.openOrderByLabel.mockResolvedValue({ order_id: 1, tracking_code: "vlabel1" });
    mocks.getOrder.mockResolvedValue(foundOrder);

    render(
      <OrderSelectStep
        onSelect={selected}
        onThisWeek={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "EAN scannen" }));
    expect(screen.getByText("Scan het Veloyd-label van een order")).toBeTruthy();
    expect(screen.getByText("Camera")).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Scan Veloyd-label met camera" }),
    );
    await user.click(screen.getByRole("button", { name: "camera-result-label" }));

    await waitFor(() =>
      expect(mocks.openOrderByLabel).toHaveBeenCalledWith("V-LABEL-1"),
    );
    expect(selected).toHaveBeenCalledWith(foundOrder);
  });

  it("requires Verder after a Veloyd error before EAN scanning is available again", async () => {
    const user = userEvent.setup();
    mocks.openOrderByLabel.mockRejectedValue(
      new ApiError(
        404,
        "Geen order gevonden voor dit Veloyd-label",
        "Geen order gevonden voor dit Veloyd-label",
      ),
    );

    render(
      <OrderSelectStep
        onSelect={vi.fn()}
        onThisWeek={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "EAN scannen" }));
    await user.click(
      screen.getByRole("button", { name: "Scan Veloyd-label met camera" }),
    );
    await user.click(screen.getByRole("button", { name: "camera-result-label" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Geen order gevonden voor dit Veloyd-label");
    expect(screen.getByRole("button", { name: "Deze week" })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "EAN scannen" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "Verder" }));
    expect(
      (screen.getByRole("button", { name: "EAN scannen" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(document.activeElement).toBe(
      screen.getByPlaceholderText("Scan Veloyd-label…"),
    );
  });

  it("sends a camera location through scanLocation", async () => {
    const user = userEvent.setup();
    render(
      <EanScanStep
        order={order({ lines: [{ ...baseLine, pick_location: "LOC-1" }] })}
        onBack={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Scan met camera" }));
    await user.click(screen.getByRole("button", { name: "camera-result-location" }));

    await waitFor(() =>
      expect(mocks.scanLocation).toHaveBeenCalledWith(1, "LOC-1"),
    );
  });

  it("sends a camera EAN through scanEan", async () => {
    const user = userEvent.setup();
    render(<EanScanStep order={order()} onBack={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Scan met camera" }));
    await user.click(screen.getByRole("button", { name: "camera-result-ean" }));

    await waitFor(() =>
      expect(mocks.scanEan).toHaveBeenCalledWith(1, "8711111111111", null),
    );
  });

  it("sends the final camera label through scanLabel", async () => {
    const user = userEvent.setup();
    render(
      <EanScanStep
        order={order({
          status: "completed",
          booked_boxes: 2,
          lines: [{ ...baseLine, booked_count: 2 }],
        })}
        onBack={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Scan met camera" }));
    await user.click(screen.getByRole("button", { name: "camera-result-label" }));

    await waitFor(() =>
      expect(mocks.scanLabel).toHaveBeenCalledWith(1, "V-LABEL-1"),
    );
  });
});
