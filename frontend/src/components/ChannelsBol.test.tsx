import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/App", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  api: {
    listOrganizations: vi.fn(),
    channelReconciliation: vi.fn(),
    bolChannelReconciliation: vi.fn(),
    bolChannelConnect: vi.fn(),
    bolChannelSync: vi.fn(),
  },
}));

import { ChannelsPage } from "@/components/channels";
import { api } from "@/lib/api";

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
});
