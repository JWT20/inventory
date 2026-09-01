import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InventoryPage } from "./inventory";

const mocks = vi.hoisted(() => ({
  listInventoryOverview: vi.fn(),
  listOrganizations: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: mocks }));
vi.mock("@/App", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: {
      role: "owner",
      is_platform_admin: false,
      enabled_modules: ["inventory"],
    },
  }),
  hasModule: () => false,
}));

beforeEach(() => {
  mocks.listInventoryOverview.mockResolvedValue([]);
  mocks.listOrganizations.mockResolvedValue([]);
});

afterEach(() => cleanup());

describe("inventory sorting", () => {
  it("cycles from A–Z through both stock directions and back", async () => {
    render(<InventoryPage />);

    const alphabetical = await screen.findByRole("button", {
      name: "Sortering: alfabetisch A–Z",
    });
    await waitFor(() =>
      expect(mocks.listInventoryOverview).toHaveBeenLastCalledWith(
        "?inventory_location=warehouse&sort=name",
      ),
    );

    fireEvent.click(alphabetical);
    const descending = await screen.findByRole("button", {
      name: "Sortering: voorraad hoog naar laag",
    });
    await waitFor(() =>
      expect(mocks.listInventoryOverview).toHaveBeenLastCalledWith(
        "?inventory_location=warehouse&sort=stock_desc",
      ),
    );

    fireEvent.click(descending);
    const ascending = await screen.findByRole("button", {
      name: "Sortering: voorraad laag naar hoog",
    });
    await waitFor(() =>
      expect(mocks.listInventoryOverview).toHaveBeenLastCalledWith(
        "?inventory_location=warehouse&sort=stock_asc",
      ),
    );

    fireEvent.click(ascending);
    expect(await screen.findByRole("button", {
      name: "Sortering: alfabetisch A–Z",
    })).toBeTruthy();
  });
});
