import { useState, useRef, useCallback, useEffect } from "react";

interface ImageSlideshowProps {
  images: string[];
  className?: string;
  maxWidth?: string;
  onImageClick?: (index: number) => void;
}

export function ImageSlideshow({
  images,
  className = "",
  maxWidth = "200px",
  onImageClick,
}: ImageSlideshowProps) {
  const [current, setCurrent] = useState(0);
  const touchStartX = useRef(0);
  const touchDeltaX = useRef(0);
  const containerRef = useRef<HTMLDivElement>(null);

  // Reset index when images change
  useEffect(() => {
    setCurrent(0);
  }, [images]);

  const total = images.length;

  const goTo = useCallback(
    (index: number) => {
      if (index < 0) setCurrent(total - 1);
      else if (index >= total) setCurrent(0);
      else setCurrent(index);
    },
    [total],
  );

  function handleTouchStart(e: React.TouchEvent) {
    touchStartX.current = e.touches[0].clientX;
    touchDeltaX.current = 0;
  }

  function handleTouchMove(e: React.TouchEvent) {
    touchDeltaX.current = e.touches[0].clientX - touchStartX.current;
  }

  function handleTouchEnd() {
    if (Math.abs(touchDeltaX.current) > 40) {
      if (touchDeltaX.current < 0) goTo(current + 1);
      else goTo(current - 1);
    }
    touchDeltaX.current = 0;
  }

  if (total === 0) {
    return (
      <div
        className={`aspect-square rounded-lg overflow-hidden bg-black flex items-center justify-center mx-auto ${className}`}
        style={{ maxWidth }}
      >
        <span className="text-muted-foreground text-xs">Geen afbeelding</span>
      </div>
    );
  }

  if (total === 1) {
    return (
      <div
        className={`aspect-square rounded-lg overflow-hidden bg-black mx-auto relative ${className}`}
        style={{ maxWidth }}
      >
        <img
          src={images[0]}
          alt="Referentie"
          className={`w-full h-full object-cover ${onImageClick ? "cursor-zoom-in" : ""}`}
          onClick={onImageClick ? () => onImageClick(0) : undefined}
        />
        {onImageClick && <ZoomBadge />}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`relative mx-auto ${className}`}
      style={{ maxWidth }}
    >
      {/* Image container */}
      <div
        className="aspect-square rounded-lg overflow-hidden bg-black relative"
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        <img
          src={images[current]}
          alt={`Referentie ${current + 1} van ${total}`}
          className={`w-full h-full object-cover transition-opacity duration-200 ${onImageClick ? "cursor-zoom-in" : ""}`}
          onClick={onImageClick ? () => onImageClick(current) : undefined}
        />

        {/* Counter badge */}
        <div className="absolute top-2 right-2 bg-black/60 text-white text-xs px-2 py-0.5 rounded-full">
          {current + 1} / {total}
        </div>

        {onImageClick && <ZoomBadge />}

        {/* Left arrow */}
        <button
          onClick={() => goTo(current - 1)}
          className="absolute left-1 top-1/2 -translate-y-1/2 w-8 h-8 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70 transition-colors"
          aria-label="Vorige afbeelding"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>

        {/* Right arrow */}
        <button
          onClick={() => goTo(current + 1)}
          className="absolute right-1 top-1/2 -translate-y-1/2 w-8 h-8 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70 transition-colors"
          aria-label="Volgende afbeelding"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      </div>

      {/* Dot indicators */}
      <div className="flex justify-center gap-1.5 mt-2">
        {images.map((_, i) => (
          <button
            key={i}
            onClick={() => goTo(i)}
            className={`w-2 h-2 rounded-full transition-colors ${
              i === current ? "bg-white" : "bg-white/30"
            }`}
            aria-label={`Afbeelding ${i + 1}`}
          />
        ))}
      </div>
    </div>
  );
}

function ZoomBadge() {
  return (
    <div className="absolute bottom-2 right-2 bg-black/60 text-white p-1 rounded-full pointer-events-none">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="7" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
        <line x1="11" y1="8" x2="11" y2="14" />
        <line x1="8" y1="11" x2="14" y2="11" />
      </svg>
    </div>
  );
}
