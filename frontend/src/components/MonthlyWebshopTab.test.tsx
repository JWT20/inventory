import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/App", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  api: {
    listOrganizations: vi.fn(),
    monthlyBookedBoxes: vi.fn(),
  },
}));

import { MonthlyBoxesPage } from "@/components/monthly-boxes";
import { api } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const month = {
  month: "2026-08",
  boxes: 0,
  bottles: 2,
  items: 0,
  item_order_count: 0,
  item_line_count: 0,
};

function report(name: string, months = [month]) {
  return {
    organization_id: 1,
    organization_name: name,
    total_boxes: 0,
    total_bottles: 2,
    total_items: 0,
    total_item_orders: 0,
    total_item_lines: 0,
    months,
  };
}

beforeEach(() => {
  vi.mocked(api.listOrganizations).mockResolvedValue([
    { id: 1, name: "Wijn van Jurjen" },
  ]);
});

async function pickTheMerchant(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("combobox"));
  await user.click(await screen.findByRole("option", { name: "Wijn van Jurjen" }));
}

describe("monthly overview webshop tab", () => {
  it("stays out of the way when this merchant has no webshop work", async () => {
    // Racesokken has no wijnadvies connection: an empty tab would only ever
    // show an empty table and leave them wondering what they are missing.
    vi.mocked(api.monthlyBookedBoxes).mockResolvedValue({
      organizations: [report("Racesokken.nl")],
      webshop: [],
      webshop_connected: false,
    });
    const user = userEvent.setup();

    render(<MonthlyBoxesPage />);
    await pickTheMerchant(user);

    await screen.findByText("augustus 2026");
    expect(screen.queryByRole("button", { name: "Webshop" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Klantorders" })).toBeNull();
  });

  it("shows the tab as soon as the merchant is connected, empty or not", async () => {
    // Jurjen has the wijnadvies connection but has picked nothing yet. Hiding
    // it until the first parcel would make the tab appear halfway a month.
    vi.mocked(api.monthlyBookedBoxes).mockResolvedValue({
      organizations: [report("Wijn van Jurjen")],
      webshop: [],
      webshop_connected: true,
    });
    const user = userEvent.setup();

    render(<MonthlyBoxesPage />);
    await pickTheMerchant(user);

    const tab = await screen.findByRole("button", { name: "Webshop" });
    await user.click(tab);
    await screen.findByText("Nog geen webshoporders gepickt voor deze handelaar");
  });

  it("offers both tabs once a webshop order has been picked", async () => {
    vi.mocked(api.monthlyBookedBoxes).mockResolvedValue({
      organizations: [report("Wijn van Jurjen")],
      webshop: [report("Wijn van Jurjen")],
      webshop_connected: true,
    });
    const user = userEvent.setup();

    render(<MonthlyBoxesPage />);
    await pickTheMerchant(user);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Webshop" })).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: "Klantorders" })).toBeTruthy();
  });

  it("does not strand the view on a tab that disappeared", async () => {
    // Switch from a merchant with webshop work to one without while the
    // webshop tab is open.
    vi.mocked(api.listOrganizations).mockResolvedValue([
      { id: 1, name: "Wijn van Jurjen" },
      { id: 2, name: "Racesokken.nl" },
    ]);
    vi.mocked(api.monthlyBookedBoxes).mockImplementation(async (orgId) =>
      String(orgId) === "1"
        ? {
            organizations: [report("Wijn van Jurjen")],
            webshop: [report("Wijn van Jurjen", [{ ...month, bottles: 9 }])],
            webshop_connected: true,
          }
        : {
            organizations: [report("Racesokken.nl")],
            webshop: [],
            webshop_connected: false,
          },
    );
    const user = userEvent.setup();

    render(<MonthlyBoxesPage />);
    await pickTheMerchant(user);
    await user.click(await screen.findByRole("button", { name: "Webshop" }));
    await screen.findByText("9 flessen");

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Racesokken.nl" }));

    // Back on the only table there is, not on an empty webshop view.
    await waitFor(() => expect(screen.queryByText("9 flessen")).toBeNull());
    await screen.findByText("augustus 2026");
  });
});
