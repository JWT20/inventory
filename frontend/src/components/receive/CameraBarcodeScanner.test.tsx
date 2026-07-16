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
      EAN_13: "EAN_13",
      QR_CODE: "QR_CODE",
    },
    BrowserMultiFormatReader,
  };
});

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
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("sm:h-[70vh]");
    expect(dialog.className).not.toContain("sm:h-auto");
    expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({
      audio: false,
      video: expect.objectContaining({ facingMode: { ideal: "environment" } }),
    }));
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
