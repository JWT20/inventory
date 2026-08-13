import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/App", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  api: {
    listOrganizations: vi.fn(),
    channelReconciliation: vi.fn(),
    bolChannelReconciliation: vi.fn(),
    listAdviceReservations: vi.fn(),
    bolChannelConnect: vi.fn(),
    bolChannelSync: vi.fn(),
    bolChannelSetMode: vi.fn(),
    bolChannelPushInventory: vi.fn(),
  },
}));

import { ChannelsPage } from "@/components/channels";
import { api } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const emptyRecon = {
  status: { connected: false, shop_domain: null, mode: null, last_synced_at: null },
  orders: [],
  unmatched_eans: [],
};

describe("bol Admin channel card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listOrganizations).mockResolvedValue([
      { id: 2, name: "Racesokken", enabled_modules: ["channel_orders"] },
    ]);
    vi.mocked(api.channelReconciliation).mockResolvedValue(emptyRecon);
    vi.mocked(api.bolChannelReconciliation).mockResolvedValue(emptyRecon);
    vi.mocked(api.listAdviceReservations).mockResolvedValue([]);
    vi.mocked(api.bolChannelConnect).mockResolvedValue({
      connected: true,
      shop_domain: null,
      mode: "observe",
      last_synced_at: null,
    });
  });

  it("connects the selected organization and refreshes channel status", async () => {
    render(<ChannelsPage />);

    const connect = await screen.findByRole("button", { name: "Koppel bol" });
    fireEvent.click(connect);

    await waitFor(() => expect(api.bolChannelConnect).toHaveBeenCalledWith(2));
    await waitFor(() =>
      expect(api.bolChannelReconciliation).toHaveBeenCalledTimes(2),
    );
  });

  it("shows open and shipped bol orders together in one list", async () => {
    vi.mocked(api.bolChannelReconciliation).mockResolvedValue({
      status: {
        connected: true,
        shop_domain: null,
        mode: "observe",
        last_synced_at: "2026-07-20T09:00:00Z",
      },
      orders: [
        {
          order_id: 10,
          external_id: "OPEN-1",
          reference: "BOL-OPEN",
          channel_reference: "OPEN-1",
          channel_fulfillment_status: "unfulfilled",
          review_reason: null,
          ordered_at: "2026-07-20T08:00:00Z",
          channel_shipped_at: null,
          status: "observed",
          matched_lines: 1,
          unmatched_eans: [],
        },
        {
          order_id: 11,
          external_id: "SHIPPED-1",
          reference: "BOL-SHIPPED",
          channel_reference: "SHIPPED-1",
          channel_fulfillment_status: "fulfilled",
          review_reason: null,
          ordered_at: "2026-07-19T08:00:00Z",
          channel_shipped_at: "2026-07-19T12:30:00Z",
          status: "observed",
          matched_lines: 2,
          unmatched_eans: ["8710000000999"],
        },
      ],
      unmatched_eans: ["8710000000999"],
    });

    render(<ChannelsPage />);

    expect(await screen.findByText("Order OPEN-1")).toBeTruthy();
    expect(screen.getByText("Order SHIPPED-1")).toBeTruthy();
    expect(screen.getByText("Openstaand")).toBeTruthy();
    expect(screen.getByText("Verzonden")).toBeTruthy();
    expect(screen.getByText(/verzonden:/)).toBeTruthy();
    expect(screen.getByText("2 gematcht")).toBeTruthy();
    expect(screen.getByText("1 ontbreken")).toBeTruthy();
    expect(screen.getAllByText(/Binnengehaalde bol-orders/)).toHaveLength(1);
  });

  it("puts bol live only after explicit confirmation", async () => {
    vi.mocked(api.bolChannelReconciliation).mockResolvedValue({
      ...emptyRecon,
      status: { ...emptyRecon.status, connected: true, mode: "observe" },
    });
    vi.mocked(api.bolChannelSetMode).mockResolvedValue({
      connected: true,
      shop_domain: null,
      mode: "live",
      last_synced_at: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ChannelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Zet live" }));

    await waitFor(() => expect(api.bolChannelSetMode).toHaveBeenCalledWith(2, "live"));
  });

  it("can align bol inventory while live", async () => {
    vi.mocked(api.bolChannelReconciliation).mockResolvedValue({
      ...emptyRecon,
      status: { ...emptyRecon.status, connected: true, mode: "live" },
    });
    vi.mocked(api.bolChannelPushInventory).mockResolvedValue({
      total: 1,
      pushed: 1,
      skipped_no_variant: 0,
      failed: 0,
    });

    render(<ChannelsPage />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Voorraad gelijktrekken" }),
    );

    await waitFor(() => expect(api.bolChannelPushInventory).toHaveBeenCalledWith(2));
  });

  it("distinguishes a reservation load failure from an empty reservation list", async () => {
    vi.mocked(api.listAdviceReservations).mockRejectedValue(
      new Error("reservation service unavailable"),
    );

    render(<ChannelsPage />);

    expect(
      await screen.findByText("Reserveringen konden niet worden geladen. Probeer het opnieuw."),
    ).toBeTruthy();
    expect(screen.queryByText("Er ligt niets apart voor de webshop.")).toBeNull();
  });
});
