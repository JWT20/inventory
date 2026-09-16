import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/App", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/api", () => ({ api: { listLocations: vi.fn(), listOrganizations: vi.fn() } }));

import { api } from "@/lib/api";
import { LocationsPage } from "./locations";

const shelf = (id: number, code: string, skus: unknown[] = []) => ({
  id, code, rij: null, kast: null, plank: null, active: true,
  created_at: "2026-09-16T00:00:00Z", skus,
});
const product = {
  sku_id: 261, sku_code: "AERO-SOK-TT-WIT-35-39", name: "Aero Fietssokken TT",
  ean: "6154438271274", organization_name: "Racesokken.nl", is_primary: true, is_bottle: false,
};

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.listOrganizations).mockResolvedValue([]);
  vi.mocked(api.listLocations).mockResolvedValue([shelf(1, "AA01", [product]), shelf(255, "BF01")]);
});

it("filters by location code, SKU code and product name without case sensitivity", async () => {
  render(<LocationsPage />);
  await screen.findByText("BF01");
  const search = screen.getByRole("searchbox", { name: "Zoeken" });
  fireEvent.change(search, { target: { value: "  bf0  " } });
  expect(screen.getByText("BF01")).toBeTruthy();
  expect(screen.queryByText("AA01")).toBeNull();
  for (const value of ["sok-tt-wit", "fietssokken"]) {
    fireEvent.change(search, { target: { value } });
    expect(screen.getByText("AA01")).toBeTruthy();
    expect(screen.queryByText("BF01")).toBeNull();
  }
  fireEvent.change(search, { target: { value: "missing" } });
  expect(screen.getByText("Geen locaties gevonden")).toBeTruthy();
  fireEvent.change(search, { target: { value: "" } });
  expect(screen.getByText("AA01")).toBeTruthy();
  expect(screen.getByText("BF01")).toBeTruthy();
  expect(api.listLocations).toHaveBeenCalledTimes(1);
});

it("searches within the selected organization's list", async () => {
  localStorage.setItem("courier.locations.selectedOrgId", "2");
  vi.mocked(api.listOrganizations).mockResolvedValue([{ id: 2, name: "Racesokken.nl" }]);
  vi.mocked(api.listLocations).mockResolvedValue([shelf(1, "AA01", [product])]);
  render(<LocationsPage />);
  await screen.findByText("AA01");
  expect(api.listLocations).toHaveBeenCalledWith(2);
  fireEvent.change(screen.getByRole("searchbox", { name: "Zoeken" }), { target: { value: "aero" } });
  expect(screen.getByText("AA01")).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox", { name: "Zoeken" }), { target: { value: "bf01" } });
  expect(screen.getByText("Geen locaties gevonden")).toBeTruthy();
});
