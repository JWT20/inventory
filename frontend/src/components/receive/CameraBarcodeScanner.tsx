import { useEffect, useRef, useState } from "react";
import { CameraOff, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type CameraBarcodeMode = "ean" | "label" | "location";

interface ScannerControls {
  stop: () => void;
}

interface CameraBarcodeScannerProps {
  open: boolean;
  mode: CameraBarcodeMode;
  title: string;
  onScan: (code: string) => void;
  onClose: () => void;
}

type ScannerStatus = "starting" | "scanning" | "found" | "error";

function stopVideo(video: HTMLVideoElement | null) {
  const stream = video?.srcObject as MediaStream | null;
  stream?.getTracks().forEach((track) => track.stop());
  if (video) video.srcObject = null;
}

function successFeedback() {
  navigator.vibrate?.(80);
  try {
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.08, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.12);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.12);
    oscillator.onended = () => void context.close();
  } catch {
    // Audio feedback is best-effort; vibration and the visual state remain.
  }
}

function cameraErrorMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") {
      return "Geef deze website toestemming om de camera te gebruiken.";
    }
    if (error.name === "NotFoundError") {
      return "Geen camera gevonden op dit apparaat.";
    }
    if (error.name === "NotReadableError") {
      return "De camera is al in gebruik door een andere app.";
    }
  }
  return "Camera kon niet worden gestart. Controleer de cameratoestemming.";
}

export function CameraBarcodeScanner({
  open,
  mode,
  title,
  onScan,
  onClose,
}: CameraBarcodeScannerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const controlsRef = useRef<ScannerControls | null>(null);
  const onScanRef = useRef(onScan);
  const [status, setStatus] = useState<ScannerStatus>("starting");
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    onScanRef.current = onScan;
  }, [onScan]);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    let scanLocked = false;
    let successTimer: number | undefined;

    setStatus("starting");
    setError(null);

    async function startScanner() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error("Camera API niet beschikbaar");
        }

        // Keep ZXing out of the initial bundle; load it only after the user opens
        // the camera scanner.
        const { BarcodeFormat, BrowserMultiFormatReader } = await import("@zxing/browser");
        if (cancelled || !videoRef.current) return;

        const reader = new BrowserMultiFormatReader(undefined, {
          delayBetweenScanAttempts: 120,
          delayBetweenScanSuccess: 500,
        });
        reader.possibleFormats =
          mode === "ean"
            ? [BarcodeFormat.EAN_13]
            : [BarcodeFormat.CODE_128, BarcodeFormat.QR_CODE];

        const controls = await reader.decodeFromConstraints(
          {
            audio: false,
            video: {
              facingMode: { ideal: "environment" },
              width: { ideal: 1280 },
              height: { ideal: 720 },
            },
          },
          videoRef.current,
          (result, _scanError, callbackControls) => {
            if (!result || scanLocked || cancelled) return;
            const code = result.getText().trim();
            if (!code) return;

            scanLocked = true;
            callbackControls.stop();
            controlsRef.current = null;
            stopVideo(videoRef.current);
            setStatus("found");
            successFeedback();
            successTimer = window.setTimeout(() => onScanRef.current(code), 120);
          },
        );

        if (cancelled || scanLocked) {
          controls.stop();
          return;
        }
        controlsRef.current = controls;
        setStatus("scanning");
      } catch (scanError) {
        if (cancelled) return;
        controlsRef.current?.stop();
        controlsRef.current = null;
        stopVideo(videoRef.current);
        setError(cameraErrorMessage(scanError));
        setStatus("error");
      }
    }

    void startScanner();

    return () => {
      cancelled = true;
      if (successTimer) window.clearTimeout(successTimer);
      controlsRef.current?.stop();
      controlsRef.current = null;
      stopVideo(videoRef.current);
    };
  }, [open, mode, retry]);

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        className="h-[85svh] max-h-[85svh] bg-black border-0 p-0 overflow-hidden text-white"
      >
        <DialogHeader className="p-4 pr-12 mb-0 bg-black">
          <DialogTitle className="text-white">{title}</DialogTitle>
          <p className="text-sm text-white/70">
            Richt de achtercamera op de volledige barcode.
          </p>
        </DialogHeader>

        <div className="relative flex-1 min-h-0 bg-black flex items-center justify-center">
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            aria-label="Live camerabeeld voor barcodescan"
            className="absolute inset-0 h-full w-full object-cover"
          />

          {(status === "starting" || status === "scanning") && (
            <div className="absolute inset-x-[8%] top-1/2 -translate-y-1/2 h-32 rounded-xl border-2 border-white shadow-[0_0_0_9999px_rgba(0,0,0,0.35)]">
              <span className="absolute left-3 right-3 top-1/2 h-0.5 -translate-y-1/2 bg-red-500/90" />
            </div>
          )}

          {status === "starting" && (
            <div className="relative z-10 flex items-center gap-2 rounded-lg bg-black/70 px-4 py-3 text-sm">
              <Loader2 className="h-5 w-5 animate-spin" />
              Camera starten…
            </div>
          )}

          {status === "found" && (
            <div className="relative z-10 rounded-lg bg-emerald-600 px-5 py-3 text-base font-semibold">
              ✓ Code gevonden
            </div>
          )}

          {status === "error" && (
            <div className="relative z-10 mx-5 rounded-lg bg-card p-5 text-card-foreground text-center">
              <CameraOff className="h-8 w-8 mx-auto mb-3 text-muted-foreground" />
              <p className="text-sm mb-4">{error}</p>
              <Button type="button" onClick={() => setRetry((value) => value + 1)}>
                Opnieuw proberen
              </Button>
            </div>
          )}
        </div>

        <div className="p-4 bg-black">
          <Button type="button" variant="secondary" className="w-full" onClick={onClose}>
            Annuleren
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
