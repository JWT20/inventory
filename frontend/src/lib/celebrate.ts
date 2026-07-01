import confetti from "canvas-confetti";

/**
 * Fire a short burst of confetti to signal an order is fully picked.
 * Used by both picking flows (EAN barcode and vision) the moment an order
 * transitions to "completed", so the picker gets an unmistakable "done" cue.
 */
export function fireCompletion() {
  confetti({ particleCount: 140, spread: 80, origin: { y: 0.6 } });
  setTimeout(
    () => confetti({ particleCount: 80, spread: 100, origin: { y: 0.5 } }),
    250,
  );
}
