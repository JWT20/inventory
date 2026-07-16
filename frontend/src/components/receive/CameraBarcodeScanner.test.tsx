import { act } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CameraBarcodeScanner } from "./CameraBarcodeScanner";

const zxing = vi.hoisted(() => ({
  callback: undefined as undefined | ((result: unknown, error: unknown, controls: { stop: () => void }) => void),
  controls: { stop: vi.fn() },
  decode: vi.fn(),
  readers: [] as Array<{ possibleFormats: unknown[] }>,
}));

vi.mock("@zxing/browser", () => {
  class BrowserMultiFormatReader {
    possibleFormats: unknown[] = [];

    constructor() {
      zxing.readers.push(this);
    }

    async decodeFromConstraints(
      constraints: MediaStreamConstraints,
      video: HTMLVideoElement,
      callback: typeof zxing.callback,
    ) {
      zxing.callback = callback;
      zxing.decode(constraints, video);
      return zxing.controls;
    }
  }

  return {
    BarcodeFormat: {
      CODE_128: "CODE_128",
      EAN_13: "EAN_13",
      QR_CODE: "QR_CODE",
    },
    BrowserMultiFormatReader,
  };
});

beforeEach(() => {
  zxing.callback = undefined;
  zxing.controls.stop.mockReset();
  zxing.decode.mockReset();
  zxing.readers.length = 0;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn() },
  });
  Object.defineProperty(navigator, "vibrate", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => cleanup());

describe("CameraBarcodeScanner", () => {
  it("uses the rear camera and only accepts EAN-13 in product mode", async () => {
    render(
      <CameraBarcodeScanner
        open
        mode="ean"
        title="Product scannen"
        onScan={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(zxing.decode).toHaveBeenCalledOnce());
    expect(zxing.decode.mock.calls[0][0]).toMatchObject({
      audio: false,
      video: { facingMode: { ideal: "environment" } },
    });
    expect(zxing.readers[0].possibleFormats).toEqual(["EAN_13"]);
  });

  it("accepts Code 128 and QR for labels and emits one scan only", async () => {
    const onScan = vi.fn();
    render(
      <CameraBarcodeScanner
        open
        mode="label"
        title="Label scannen"
        onScan={onScan}
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(zxing.callback).toBeDefined());
    expect(zxing.readers[0].possibleFormats).toEqual(["CODE_128", "QR_CODE"]);

    const result = { getText: () => "  V-123  " };
    await act(async () => {
      zxing.callback?.(result, undefined, zxing.controls);
      zxing.callback?.(result, undefined, zxing.controls);
      await new Promise((resolve) => window.setTimeout(resolve, 150));
    });

    expect(onScan).toHaveBeenCalledOnce();
    expect(onScan).toHaveBeenCalledWith("V-123");
    expect(zxing.controls.stop).toHaveBeenCalledOnce();
  });

  it("shows an actionable message when camera permission is denied", async () => {
    zxing.decode.mockImplementationOnce(() => {
      throw new DOMException("denied", "NotAllowedError");
    });

    render(
      <CameraBarcodeScanner
        open
        mode="location"
        title="Locatie scannen"
        onScan={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("Geef deze website toestemming om de camera te gebruiken."),
    ).toBeTruthy();
  });

  it("stops the scanner when the dialog closes", async () => {
    const { rerender } = render(
      <CameraBarcodeScanner
        open
        mode="location"
        title="Locatie scannen"
        onScan={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(zxing.decode).toHaveBeenCalledOnce());
    rerender(
      <CameraBarcodeScanner
        open={false}
        mode="location"
        title="Locatie scannen"
        onScan={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(zxing.controls.stop).toHaveBeenCalledOnce();
  });
});
