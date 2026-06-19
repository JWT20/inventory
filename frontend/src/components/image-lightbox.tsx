import { useCallback, useEffect, useRef, useState } from "react";

interface ImageLightboxProps {
  images: string[];
  captions?: string[];
  startIndex?: number;
  open: boolean;
  onClose: () => void;
}

const MIN_SCALE = 1;
const MAX_SCALE = 5;
const DOUBLE_TAP_SCALE = 2.5;
const SWIPE_THRESHOLD = 50;

export function ImageLightbox({
  images,
  captions,
  startIndex = 0,
  open,
  onClose,
}: ImageLightboxProps) {
  const [index, setIndex] = useState(startIndex);
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);

  const total = images.length;
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  // Gesture state (refs to avoid re-renders during gestures)
  const panStart = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const pinchStart = useRef<{ dist: number; scale: number; cx: number; cy: number; tx: number; ty: number } | null>(null);
  const swipeStart = useRef<{ x: number; y: number; t: number } | null>(null);
  const lastTap = useRef<{ t: number; x: number; y: number } | null>(null);

  const resetTransform = useCallback(() => {
    setScale(1);
    setTx(0);
    setTy(0);
  }, []);

  // Reset to startIndex / transform when (re)opened
  useEffect(() => {
    if (open) {
      setIndex(startIndex);
      resetTransform();
    }
  }, [open, startIndex, resetTransform]);

  // Reset transform when switching image
  useEffect(() => {
    resetTransform();
  }, [index, resetTransform]);

  // Escape + arrow keys
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") goTo(index - 1);
      else if (e.key === "ArrowRight") goTo(index + 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, index, total]);

  // Lock body scroll while open
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const goTo = useCallback(
    (next: number) => {
      if (total === 0) return;
      const wrapped = ((next % total) + total) % total;
      setIndex(wrapped);
    },
    [total],
  );

  // Clamp pan so image stays in view
  const clampPan = useCallback(
    (nextScale: number, nextTx: number, nextTy: number) => {
      const el = imgRef.current;
      const container = containerRef.current;
      if (!el || !container) return { tx: nextTx, ty: nextTy };
      const cw = container.clientWidth;
      const ch = container.clientHeight;
      // Natural displayed size at scale=1 (object-contain)
      const iw = el.clientWidth;
      const ih = el.clientHeight;
      const scaledW = iw * nextScale;
      const scaledH = ih * nextScale;
      const maxX = Math.max(0, (scaledW - cw) / 2);
      const maxY = Math.max(0, (scaledH - ch) / 2);
      return {
        tx: Math.max(-maxX, Math.min(maxX, nextTx)),
        ty: Math.max(-maxY, Math.min(maxY, nextTy)),
      };
    },
    [],
  );

  // Wheel zoom (cursor-centered)
  function handleWheel(e: React.WheelEvent) {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const cx = e.clientX - rect.left - rect.width / 2;
    const cy = e.clientY - rect.top - rect.height / 2;
    const delta = -e.deltaY * 0.0015;
    const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * (1 + delta)));
    if (nextScale === scale) return;
    const k = nextScale / scale;
    const nextTx = cx - (cx - tx) * k;
    const nextTy = cy - (cy - ty) * k;
    const clamped = clampPan(nextScale, nextScale === 1 ? 0 : nextTx, nextScale === 1 ? 0 : nextTy);
    setScale(nextScale);
    setTx(clamped.tx);
    setTy(clamped.ty);
  }

  function handleDoubleClick(e: React.MouseEvent) {
    e.stopPropagation();
    if (scale > 1) {
      resetTransform();
    } else {
      const container = containerRef.current;
      if (!container) {
        setScale(DOUBLE_TAP_SCALE);
        return;
      }
      const rect = container.getBoundingClientRect();
      const cx = e.clientX - rect.left - rect.width / 2;
      const cy = e.clientY - rect.top - rect.height / 2;
      const k = DOUBLE_TAP_SCALE;
      const nextTx = cx - cx * k;
      const nextTy = cy - cy * k;
      const clamped = clampPan(k, nextTx, nextTy);
      setScale(k);
      setTx(clamped.tx);
      setTy(clamped.ty);
    }
  }

  // Mouse drag pan when zoomed
  function handleMouseDown(e: React.MouseEvent) {
    if (scale <= 1) return;
    panStart.current = { x: e.clientX, y: e.clientY, tx, ty };
  }
  function handleMouseMove(e: React.MouseEvent) {
    if (!panStart.current) return;
    e.preventDefault();
    const dx = e.clientX - panStart.current.x;
    const dy = e.clientY - panStart.current.y;
    const clamped = clampPan(scale, panStart.current.tx + dx, panStart.current.ty + dy);
    setTx(clamped.tx);
    setTy(clamped.ty);
  }
  function endMouseDrag() {
    panStart.current = null;
  }

  // Touch: pinch zoom + pan + swipe-to-navigate (when not zoomed)
  function distance(t1: React.Touch, t2: React.Touch) {
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    return Math.hypot(dx, dy);
  }

  function handleTouchStart(e: React.TouchEvent) {
    if (e.touches.length === 2) {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const cx = (t1.clientX + t2.clientX) / 2 - rect.left - rect.width / 2;
      const cy = (t1.clientY + t2.clientY) / 2 - rect.top - rect.height / 2;
      pinchStart.current = {
        dist: distance(t1, t2),
        scale,
        cx,
        cy,
        tx,
        ty,
      };
      panStart.current = null;
      swipeStart.current = null;
    } else if (e.touches.length === 1) {
      const t = e.touches[0];
      // Double-tap detection
      const now = Date.now();
      const last = lastTap.current;
      if (last && now - last.t < 300 && Math.hypot(t.clientX - last.x, t.clientY - last.y) < 30) {
        // Synthesize double-click
        if (scale > 1) {
          resetTransform();
        } else {
          const container = containerRef.current;
          if (container) {
            const rect = container.getBoundingClientRect();
            const cx = t.clientX - rect.left - rect.width / 2;
            const cy = t.clientY - rect.top - rect.height / 2;
            const k = DOUBLE_TAP_SCALE;
            const nextTx = cx - cx * k;
            const nextTy = cy - cy * k;
            const clamped = clampPan(k, nextTx, nextTy);
            setScale(k);
            setTx(clamped.tx);
            setTy(clamped.ty);
          }
        }
        lastTap.current = null;
        return;
      }
      lastTap.current = { t: now, x: t.clientX, y: t.clientY };

      if (scale > 1) {
        panStart.current = { x: t.clientX, y: t.clientY, tx, ty };
        swipeStart.current = null;
      } else {
        swipeStart.current = { x: t.clientX, y: t.clientY, t: now };
        panStart.current = null;
      }
      pinchStart.current = null;
    }
  }

  function handleTouchMove(e: React.TouchEvent) {
    if (e.touches.length === 2 && pinchStart.current) {
      e.preventDefault();
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const newDist = distance(t1, t2);
      const ratio = newDist / pinchStart.current.dist;
      const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, pinchStart.current.scale * ratio));
      const k = nextScale / pinchStart.current.scale;
      const nextTx = pinchStart.current.cx - (pinchStart.current.cx - pinchStart.current.tx) * k;
      const nextTy = pinchStart.current.cy - (pinchStart.current.cy - pinchStart.current.ty) * k;
      const clamped = clampPan(nextScale, nextScale === 1 ? 0 : nextTx, nextScale === 1 ? 0 : nextTy);
      setScale(nextScale);
      setTx(clamped.tx);
      setTy(clamped.ty);
    } else if (e.touches.length === 1 && panStart.current) {
      e.preventDefault();
      const t = e.touches[0];
      const dx = t.clientX - panStart.current.x;
      const dy = t.clientY - panStart.current.y;
      const clamped = clampPan(scale, panStart.current.tx + dx, panStart.current.ty + dy);
      setTx(clamped.tx);
      setTy(clamped.ty);
    }
  }

  function handleTouchEnd(e: React.TouchEvent) {
    if (pinchStart.current && e.touches.length < 2) {
      pinchStart.current = null;
    }
    if (panStart.current && e.touches.length === 0) {
      panStart.current = null;
    }
    if (swipeStart.current && e.touches.length === 0) {
      const start = swipeStart.current;
      swipeStart.current = null;
      const changed = e.changedTouches[0];
      if (!changed) return;
      const dx = changed.clientX - start.x;
      const dy = changed.clientY - start.y;
      if (Math.abs(dx) > SWIPE_THRESHOLD && Math.abs(dx) > Math.abs(dy) * 1.5) {
        if (dx < 0) goTo(index + 1);
        else goTo(index - 1);
      }
    }
  }

  function handleBackdropClick() {
    if (scale === 1) onClose();
  }

  if (!open || total === 0) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/95 flex items-center justify-center"
      onClick={handleBackdropClick}
    >
      {/* Close button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="absolute top-3 right-3 z-10 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center"
        aria-label="Sluiten"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>

      {/* Counter */}
      {total > 1 && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 bg-white/10 text-white text-sm px-3 py-1 rounded-full">
          {index + 1} / {total}
        </div>
      )}

      {/* Image area */}
      <div
        ref={containerRef}
        className="relative w-full h-full flex items-center justify-center overflow-hidden touch-none select-none"
        onWheel={handleWheel}
        onDoubleClick={handleDoubleClick}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={endMouseDrag}
        onMouseLeave={endMouseDrag}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        onTouchCancel={handleTouchEnd}
        style={{ cursor: scale > 1 ? (panStart.current ? "grabbing" : "grab") : "zoom-in" }}
      >
        <img
          ref={imgRef}
          src={images[index]}
          alt={`Afbeelding ${index + 1} van ${total}`}
          draggable={false}
          onClick={(e) => e.stopPropagation()}
          className="max-w-full max-h-full object-contain"
          style={{
            transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
            transformOrigin: "center center",
            transition: panStart.current || pinchStart.current ? "none" : "transform 0.15s ease-out",
            willChange: "transform",
          }}
        />
      </div>

      {/* Navigation arrows */}
      {total > 1 && (
        <>
          <button
            onClick={(e) => {
              e.stopPropagation();
              goTo(index - 1);
            }}
            className="absolute left-3 top-1/2 -translate-y-1/2 z-10 w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center"
            aria-label="Vorige afbeelding"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              goTo(index + 1);
            }}
            className="absolute right-3 top-1/2 -translate-y-1/2 z-10 w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center"
            aria-label="Volgende afbeelding"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        </>
      )}

      {/* Caption */}
      {captions?.[index] && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 max-w-[90%] truncate bg-white/10 text-white text-sm px-3 py-1 rounded-full text-center">
          {captions[index]}
        </div>
      )}
    </div>
  );
}
