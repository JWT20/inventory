import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OrdersPage } from "./orders";
import { SKUsPage } from "./skus";
import { OrderCard } from "./receive/OrderCard";
import type { Order } from "./receive/types";

const mocks = vi.hoisted(() => ({
  listOrders: vi.fn(),
  listSKUs: vi.fn(),
}));

const eanUser = {
  role: "owner",
  is_platform_admin: false,
  enabled_modules: ["inventory", "orders", "barcode_picking", "channel_orders"],
};

vi.mock("@/lib/api", () => ({ api: mocks }));
vi.mock("@/App", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: eanUser }),
  hasModule: (user: typeof eanUser | null, module: string) =>
    Boolean(user?.enabled_modules.includes(module)),
}));

beforeEach(() => {
  mocks.listOrders.mockResolvedValue([]);
  mocks.listSKUs.mockResolvedValue([
    {
      id: 1,
      sku_code: "EAN-001",
      name: "EAN product",
      description: null,
      active: true,
      category: "overig",
      attributes: {},
      supplier_id: null,
      supplier_name: null,
      is_bottle: false,
      product_type: "barcode",
      ean: "8712345678906",
      image_count: 0,
    },
  ]);
});

afterEach(() => cleanup());

describe("EAN merchant UI", () => {
  it("hides manual order creation for barcode-only organizations", async () => {
    render(<OrdersPage />);

    await waitFor(() => expect(mocks.listOrders).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "+ Order" })).toBeNull();
  });

  it("does not mention reference images for barcode products", async () => {
    render(<SKUsPage />);

    expect(await screen.findByText("EAN product")).toBeTruthy();
    expect(screen.queryByText(/referentiebeeld/i)).toBeNull();
  });

  it("shows arrival date and items on scanner order cards", () => {
    const order: Order = {
      id: 1,
      reference: "SHOP-1",
      status: "active",
      channel: "shopify",
      pick_method: "barcode",
      created_at: "2026-07-21T09:00:00Z",
      ordered_at: "2026-07-21T08:00:00Z",
      customer_name: "Webklant",
      total_boxes: 0,
      booked_boxes: 0,
      total_bottles: 0,
      booked_bottles: 0,
      total_items: 4,
      booked_items: 2,
      lines: [
        {
          id: 1,
          sku_id: 1,
          sku_code: "EAN-001",
          sku_name: "EAN product",
          delivery_day: "thursday",
          customer_name: "Webklant",
          quantity: 4,
          booked_count: 2,
          is_bottle: false,
        },
      ],
    };

    render(<OrderCard order={order} onSelect={() => {}} />);

    expect(screen.getByText(/Binnengekomen di 21 jul/i)).toBeTruthy();
    expect(screen.getByText(/2\/4 items geboekt/i)).toBeTruthy();
    expect(screen.queryByText("Do")).toBeNull();
  });
});
