import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const weeklyOrderSummary = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    weeklyOrderSummary: (...args: unknown[]) => weeklyOrderSummary(...args),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { role: "owner" } }),
}));

vi.mock("@/App", () => ({
  toast: { error: vi.fn() },
}));

import { WeeklySummaryPage } from "./weekly-summary";

const summary = {
  week: "2026-W34",
  group_by: "customer",
  suppliers: [],
  customers: [
    {
      customer_id: 1,
      customer_name: "Anna de Vries",
      lines: [],
      customer_total_quantity: 6,
      customer_total_boxes: 1,
      customer_total_bottles: 0,
      customer_total_value: 60,
    },
  ],
  sellable_stock: [
    {
      sku_id: 7,
      sku_code: "W-ALB-PAI",
      sku_name: "Albamar 'PAI' Albarino Wit",
      webshop: 0,
      store: 6,
      total: 6,
      warehouse_boxes: 2,
      warehouse_bottles: 0,
    },
  ],
  grand_total_quantity: 6,
  grand_total_boxes: 1,
  grand_total_bottles: 0,
  grand_total_value: 60,
};

beforeEach(() => {
  localStorage.clear();
  weeklyOrderSummary.mockReset();
  weeklyOrderSummary.mockResolvedValue(summary);
});

afterEach(() => cleanup());

describe("weekly summary tabs", () => {
  it("shows the shelf on its own tab instead of above the groupings", async () => {
    render(<WeeklySummaryPage />);
    await waitFor(() => expect(weeklyOrderSummary).toHaveBeenCalled());

    // The grouping tab shows the week's orders and not the shelf.
    expect(await screen.findByText("Anna de Vries")).toBeTruthy();
    expect(screen.queryByText("Webshop & winkel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Voorraad" }));

    expect(screen.getByText("Webshop & winkel")).toBeTruthy();
    expect(screen.getByText("Albamar 'PAI' Albarino Wit")).toBeTruthy();
    expect(screen.queryByText("Anna de Vries")).toBeNull();
  });

  it("does not refetch the week when the shelf tab is opened", async () => {
    render(<WeeklySummaryPage />);
    await waitFor(() => expect(weeklyOrderSummary).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Voorraad" }));

    // The shelf rides on the response already loaded; asking again would cost a
    // round trip for data that is identical.
    expect(weeklyOrderSummary).toHaveBeenCalledTimes(1);
  });

  it("returns to the grouping the user was last looking at", async () => {
    render(<WeeklySummaryPage />);
    await waitFor(() => expect(weeklyOrderSummary).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Per leverancier" }));
    await waitFor(() => expect(weeklyOrderSummary).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Voorraad" }));

    // Leaving on the shelf must not overwrite which grouping to come back to.
    expect(localStorage.getItem("weekly-summary:group-by")).toBe("supplier");
    expect(localStorage.getItem("weekly-summary:view")).toBe("stock");
  });

  it("hides the week picker on the shelf, which is not a week", async () => {
    render(<WeeklySummaryPage />);
    await waitFor(() => expect(weeklyOrderSummary).toHaveBeenCalled());

    expect(screen.getByRole("button", { name: "Vandaag" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Voorraad" }));

    const weekNav = screen.getByRole("button", { name: "Vandaag" }).parentElement;
    expect(weekNav?.className).toContain("hidden");
  });
});
