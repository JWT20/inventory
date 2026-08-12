import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScanStep } from "./ScanStep";
import type { NextPick, Order } from "./types";

const mocks = vi.hoisted(() => ({
  listOrders: vi.fn(),
  nextPick: vi.fn(),
  weeklyPickPhotos: vi.fn(),
  bookBox: vi.fn(),
  registerReferenceAndBook: vi.fn(),
  toastError: vi.fn(),
  toastDismiss: vi.fn(),
  getUserMedia: vi.fn(),
  stopTrack: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    listOrders: mocks.listOrders,
    nextPick: mocks.nextPick,
    weeklyPickPhotos: mocks.weeklyPickPhotos,
    bookBox: mocks.bookBox,
    registerReferenceAndBook: mocks.registerReferenceAndBook,
  },
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
  toast: {
    error: mocks.toastError,
    dismiss: mocks.toastDismiss,
  },
}));

const order: Order = {
  id: 1,
  reference: "ORD-1",
  status: "active",
  pick_method: "vision",
  delivery_week: "2026-W33",
  created_at: "2026-08-12T07:00:00Z",
  organization_id: 1,
  customer_name: "Testklant",
  total_boxes: 1,
  booked_boxes: 0,
  total_bottles: 0,
  booked_bottles: 0,
  total_items: 1,
  booked_items: 0,
};

const nextPick: NextPick = {
  sku_id: 10,
  sku_name: "Testwijn",
  order_line_id: 20,
  image_url: null,
  remaining_quantity: 1,
  source: "this_order",
  order_id: 1,
  customer_name: "Testklant",
};

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.getUserMedia.mockResolvedValue({
    getTracks: () => [{ stop: mocks.stopTrack }],
  } as unknown as MediaStream);
  mocks.listOrders.mockResolvedValue([order]);
  mocks.nextPick.mockResolvedValue(nextPick);
  mocks.weeklyPickPhotos.mockResolvedValue([]);
  mocks.bookBox.mockRejectedValue(
    new Error("Deze doos staat niet open in de open orders"),
  );

  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: mocks.getUserMedia },
  });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/jpeg;base64,scan",
  );
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
    (callback) => callback(new Blob(["scan"], { type: "image/jpeg" })),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderScanStep() {
  return render(
    <ScanStep
      order={order}
      scanMode="box"
      onScanModeChange={vi.fn()}
      onBooked={vi.fn()}
      onBack={vi.fn()}
    />,
  );
}

describe("ScanStep feedback", () => {
  it("keeps a scan error visible with a close button", async () => {
    const user = userEvent.setup();
    renderScanStep();

    await user.click(await screen.findByRole("button", { name: "Scan" }));

    await waitFor(() =>
      expect(mocks.toastError).toHaveBeenCalledWith(
        "Deze doos staat niet open in de open orders",
        {
          id: "scan-feedback",
          duration: Infinity,
          closeButton: true,
        },
      ),
    );
  });

  it("dismisses the previous feedback on a new scan and on unmount", async () => {
    const user = userEvent.setup();
    const view = renderScanStep();
    const scanButton = await screen.findByRole("button", { name: "Scan" });

    await user.click(scanButton);
    await waitFor(() => expect(mocks.bookBox).toHaveBeenCalledTimes(1));
    expect(mocks.toastDismiss).toHaveBeenCalledTimes(1);
    expect(mocks.toastDismiss).toHaveBeenLastCalledWith("scan-feedback");

    await user.click(scanButton);
    await waitFor(() => expect(mocks.bookBox).toHaveBeenCalledTimes(2));
    expect(mocks.toastDismiss).toHaveBeenCalledTimes(2);
    expect(mocks.toastDismiss).toHaveBeenLastCalledWith("scan-feedback");

    view.unmount();
    expect(mocks.toastDismiss).toHaveBeenCalledTimes(3);
    expect(mocks.toastDismiss).toHaveBeenLastCalledWith("scan-feedback");
  });
});
