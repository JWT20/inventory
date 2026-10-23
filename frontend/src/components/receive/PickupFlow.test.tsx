import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReceivePage } from "./index";
import { EanScanStep } from "./EanScanStep";
import { ResultStep } from "./ResultStep";
import { ConfirmStep } from "./ConfirmStep";
import { OrdersPage } from "../orders";
import type { BookingResult, Order } from "./types";

const mocks = vi.hoisted(() => ({
  listOrders: vi.fn(), collectOrder: vi.fn(), scanEan: vi.fn(), scanLabel: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mocks, ApiError: class extends Error {} }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { role: "courier" } }),
  hasModule: () => true,
}));
vi.mock("@/App", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/celebrate", () => ({ fireCompletion: vi.fn() }));
vi.mock("./DistributionPanel", () => ({ DistributionPanel: () => null }));

const pickup: Order = {
  id: 1, reference: "ADV-TEST", channel_reference: "JUR-2026-123",
  channel: "advice", is_pickup: true, status: "completed", pick_method: "vision",
  customer_name: "Anna de Vries", created_at: "2026-01-01T10:00:00Z",
  total_boxes: 0, booked_boxes: 0, total_bottles: 1, booked_bottles: 1,
  total_items: 0, booked_items: 0,
  lines: [{
    id: 10, sku_id: 20, sku_name: "Testwijn", sku_code: "WIJN-1",
    customer_name: "Anna de Vries", delivery_day: "", quantity: 1,
    booked_count: 1, is_bottle: true,
  }],
};
const booking: BookingResult = {
  id: 30, order_id: 1, order_line_id: 10, order_reference: "ADV-TEST",
  sku_name: "Testwijn", sku_code: "WIJN-1", klant: "Anna de Vries",
  rolcontainer: "", order_completed: true, remaining_quantity: 0,
};

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.listOrders.mockResolvedValue([pickup]);
  mocks.collectOrder.mockResolvedValue({ ...pickup, status: "collected" });
});
afterEach(cleanup);

describe("Stavanger pickup flow", () => {
  it("keeps old pickups visible in Orders until collection, then shows them in history", async () => {
    const user = userEvent.setup();
    const order = { ...pickup, remarks: "", organization_name: "Jurjen", lines: [] };
    mocks.listOrders.mockResolvedValue([order]);
    render(<OrdersPage />);
    await user.click(await screen.findByText("ADV-TEST"));
    expect(screen.getByRole("button", { name: "Afgehaald" })).toBeTruthy();
    mocks.listOrders.mockResolvedValue([{ ...order, status: "collected" }]);
    await user.click(screen.getByRole("button", { name: "Afgehaald" }));
    await waitFor(() => expect(screen.queryByText("ADV-TEST")).toBeNull());
    await user.click(screen.getByRole("button", { name: /Historie/ }));
    expect(await screen.findByText("Afgehaald")).toBeTruthy();
    expect(screen.getByText(/Anna de Vries/)).toBeTruthy();
  });

  it.each(["vision", "barcode"] as const)("finds and hands over a picked %s order", async (pick_method) => {
    const user = userEvent.setup();
    mocks.listOrders.mockResolvedValue([{ ...pickup, pick_method }]);
    render(<ReceivePage />);
    await screen.findByText("Klaar voor afhalen");
    const search = screen.getByRole("textbox", { name: "Zoek op klant of ordernummer" });
    await user.type(search, "onbekend");
    expect(screen.queryByText("ADV-TEST")).toBeNull();
    await user.clear(search);
    await user.type(search, "anna");
    expect(screen.getByText("ADV-TEST")).toBeTruthy();
    await user.clear(search);
    await user.type(search, "jur-2026-123");
    await user.click(screen.getByText("ADV-TEST"));

    expect(screen.getByText("Anna de Vries")).toBeTruthy();
    expect(screen.getByText("JUR-2026-123")).toBeTruthy();
    expect(screen.queryByText("Zet op rolcontainer")).toBeNull();
    expect(screen.queryByPlaceholderText("Scan het verzendlabel…")).toBeNull();
    expect(mocks.collectOrder).not.toHaveBeenCalled();

    // Leaving the order does not hand it over: it remains available to reopen.
    await user.click(screen.getByRole("button", { name: "Terug naar orders" }));
    await user.click(await screen.findByText("ADV-TEST"));
    mocks.listOrders.mockResolvedValue([]);
    await user.click(screen.getByRole("button", { name: "Afgehaald" }));
    await waitFor(() => expect(mocks.collectOrder).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.queryByText("ADV-TEST")).toBeNull());
  });

  it("goes straight from the last EAN to pickup instructions", async () => {
    const user = userEvent.setup();
    mocks.scanEan.mockResolvedValue({
      ...booking, booking_id: 30, booked_quantity: 1, remaining_quantity: 0,
    });
    render(<EanScanStep order={{
      ...pickup, status: "active", pick_method: "barcode",
      lines: pickup.lines?.map((line) => ({ ...line, booked_count: 0 })),
    }} onBack={vi.fn()} />);
    await user.type(screen.getByPlaceholderText("Scan of typ de EAN…"), "8711111111111{Enter}");
    await screen.findByRole("button", { name: "Afgehaald" });
    expect(screen.queryByPlaceholderText("Scan het verzendlabel…")).toBeNull();
    expect(mocks.scanLabel).not.toHaveBeenCalled();
  });

  it("keeps the order and an actionable error if handover fails", async () => {
    const user = userEvent.setup();
    mocks.collectOrder.mockRejectedValue(new Error("Verbinding verbroken"));
    render(<ResultStep booking={booking} order={pickup} scanMode="bottle" onNext={vi.fn()} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Afgehaald" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Verbinding verbroken");
    expect((screen.getByRole("button", { name: "Afgehaald" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText("Anna de Vries")).toBeTruthy();
  });

  it("shows the private customer instead of a container while confirming a photo", () => {
    render(<ConfirmStep order={pickup} scanMode="bottle" onConfirmed={vi.fn()} onReject={vi.fn()}
      confirmation={{
        needs_confirmation: true, confirmation_token: "token", confidence: 0.9,
        sku_code: "WIJN-1", sku_name: "Testwijn", klant: "Anna de Vries",
        rolcontainer: "KLANT ANNA DE VRIES", scan_image_url: "scan.jpg",
        reference_image_url: "wine.jpg", remaining_quantity: 1,
      }}
    />);
    expect(screen.getByText("Klaarzetten voor afhalen — Stavangerweg")).toBeTruthy();
    expect(screen.getAllByText("Anna de Vries").length).toBeGreaterThan(0);
    expect(screen.queryByText("Zet op rolcontainer")).toBeNull();
  });
});
