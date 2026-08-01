import { act } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CameraBarcodeScanner } from "./CameraBarcodeScanner";

const zxing = vi.hoisted(() => ({
  callback: undefined as undefined | ((result: unknown, error: unknown, controls: { stop: () => void }) => void),
  controls: { stop: vi.fn() },
  decode: vi.fn(),
  readers: [] as Array<{
    hints: Map<unknown, unknown>;
    possibleFormats: unknown[];
  }>,
}));

vi.mock("@zxing/browser", () => {
  class BrowserMultiFormatReader {
    possibleFormats: unknown[] = [];

    constructor(public hints: Map<unknown, unknown>) {
      zxing.readers.push(this);
    }

    async decodeFromStream(
      stream: MediaStream,
      video: HTMLVideoElement,
      callback: typeof zxing.callback,
    ) {
      zxing.callback = callback;
      return zxing.decode(stream, video, callback);
    }
  }

  return {
    BarcodeFormat: {
      CODE_128: "CODE_128",
      CODE_39: "CODE_39",
      CODE_93: "CODE_93",
      DATA_MATRIX: "DATA_MATRIX",
      EAN_8: "EAN_8",
      EAN_13: "EAN_13",
      ITF: "ITF",
      PDF_417: "PDF_417",
      QR_CODE: "QR_CODE",
    },
    BrowserMultiFormatReader,
  };
});

vi.mock("@zxing/library", () => ({
  DecodeHintType: {
    TRY_HARDER: "TRY_HARDER",
  },
}));

function createMediaStream() {
  const track = { stop: vi.fn() };
  const stream = {
    getTracks: () => [track],
  } as unknown as MediaStream;
  return { stream, track };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

let getUserMedia: ReturnType<typeof vi.fn>;

beforeEach(() => {
  const { stream } = createMediaStream();
  zxing.callback = undefined;
  zxing.controls.stop.mockReset();
  zxing.decode.mockReset();
  zxing.decode.mockImplementation(
    (activeStream: MediaStream, video: HTMLVideoElement) => {
      video.srcObject = activeStream;
      zxing.controls.stop.mockImplementation(() => {
        activeStream.getTracks().forEach((track) => track.stop());
        video.srcObject = null;
      });
      return Promise.resolve(zxing.controls);
    },
  );
  zxing.readers.length = 0;
  getUserMedia = vi.fn().mockResolvedValue(stream);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  Object.defineProperty(navigator, "vibrate", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => cleanup());

describe("CameraBarcodeScanner", () => {
  it("uses the rear camera and robust one-dimensional decoding in product mode", async () => {
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
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("sm:h-[70vh]");
    expect(dialog.className).not.toContain("sm:h-auto");
    expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({
      audio: false,
      video: expect.objectContaining({
        facingMode: { ideal: "environment" },
        // 1080p and continuous focus are what make long Code 128 labels
        // decodable; keep them asserted so a cleanup cannot drop them.
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        advanced: [{ focusMode: "continuous" }],
      }),
    }));
    expect(zxing.readers[0].hints.get("TRY_HARDER")).toBe(true);
    expect(zxing.readers[0].possibleFormats).toEqual(["EAN_13", "EAN_8"]);
  });

  it("accepts common shipping-label formats and emits one scan only", async () => {
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
    expect(zxing.readers[0].hints.get("TRY_HARDER")).toBe(true);
    expect(zxing.readers[0].possibleFormats).toEqual([
      "CODE_128",
      "CODE_39",
      "CODE_93",
      "ITF",
      "QR_CODE",
      "DATA_MATRIX",
      "PDF_417",
    ]);

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
    getUserMedia.mockRejectedValueOnce(
      new DOMException("denied", "NotAllowedError"),
    );

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

  it("does not let an older start detach the newest camera stream", async () => {
    const firstMedia = createMediaStream();
    const secondMedia = createMediaStream();
    const firstResult = deferred<{ stop: () => void }>();
    let firstVideo: HTMLVideoElement | null = null;
    let secondVideo: HTMLVideoElement | null = null;
    const firstControls = {
      stop: vi.fn(() => {
        firstMedia.track.stop();
        if (firstVideo) firstVideo.srcObject = null;
      }),
    };
    const secondControls = {
      stop: vi.fn(() => {
        secondMedia.track.stop();
        if (secondVideo) secondVideo.srcObject = null;
      }),
    };

    getUserMedia
      .mockResolvedValueOnce(firstMedia.stream)
      .mockResolvedValueOnce(secondMedia.stream);
    zxing.decode
      .mockImplementationOnce((stream: MediaStream, video: HTMLVideoElement) => {
        firstVideo = video;
        video.srcObject = stream;
        return firstResult.promise;
      })
      .mockImplementationOnce((stream: MediaStream, video: HTMLVideoElement) => {
        secondVideo = video;
        video.srcObject = stream;
        return Promise.resolve(secondControls);
      });

    const props = {
      mode: "label" as const,
      title: "Label scannen",
      onScan: vi.fn(),
      onClose: vi.fn(),
    };
    const { rerender } = render(<CameraBarcodeScanner open {...props} />);

    await waitFor(() => expect(zxing.decode).toHaveBeenCalledOnce());
    rerender(<CameraBarcodeScanner open={false} {...props} />);
    rerender(<CameraBarcodeScanner open {...props} />);

    await waitFor(() => expect(zxing.decode).toHaveBeenCalledTimes(2));
    const video = screen.getByLabelText(
      "Live camerabeeld voor barcodescan",
    ) as HTMLVideoElement;
    expect(video.srcObject).toBe(secondMedia.stream);

    await act(async () => {
      firstResult.resolve(firstControls);
      await firstResult.promise;
    });

    await waitFor(() => expect(firstControls.stop).toHaveBeenCalledOnce());
    expect(video.srcObject).toBe(secondMedia.stream);
    expect(secondMedia.track.stop).not.toHaveBeenCalled();
  });
});
