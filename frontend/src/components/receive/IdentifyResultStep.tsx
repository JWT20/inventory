import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ImageSlideshow } from "@/components/image-slideshow";
import { ImageLightbox } from "@/components/image-lightbox";
import type { IdentifyResult } from "./types";

export function IdentifyResultStep({
  result,
  onNext,
  onDone,
}: {
  result: IdentifyResult | null;
  onNext: () => void;
  onDone: () => void;
}) {
  const [lightbox, setLightbox] = useState<{ images: string[]; index: number } | null>(null);

  if (!result) return null;

  const referenceImages = result.reference_image_urls ?? [];

  return (
    <>
      {result.needs_confirmation ? (
        <div className="p-6 rounded-lg bg-yellow-600/20 border-2 border-yellow-600 text-center mb-4">
          <p className="text-yellow-400 text-2xl font-bold mb-2">
            Controleer resultaat
          </p>
          <p className="text-yellow-300 text-xl font-black">
            {result.sku_name}
          </p>
          <p className="text-yellow-400/80 text-sm mt-2">
            Lage betrouwbaarheid — controleer of dit klopt
          </p>
        </div>
      ) : (
        <div className="p-6 rounded-lg bg-blue-600/20 border-2 border-blue-600 text-center mb-4">
          <p className="text-blue-400 text-2xl font-bold mb-2">
            Product herkend
          </p>
          <p className="text-blue-300 text-xl font-black">
            {result.sku_name}
          </p>
        </div>
      )}

      <Card className="p-4 mb-4">
        <div className="space-y-1 mb-3">
          <p className="text-sm">
            <span className="text-muted-foreground">Product:</span>{" "}
            <span className="font-semibold">{result.sku_name}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">SKU:</span>{" "}
            <span className="font-mono">{result.sku_code}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">Zekerheid:</span>{" "}
            {Math.round(result.confidence * 100)}%
          </p>
        </div>

        {/* Scan vs referentie vergelijking */}
        {(result.scan_image_url || (result.reference_image_urls && result.reference_image_urls.length > 0)) && (
          <div className="grid grid-cols-2 gap-3">
            {result.scan_image_url && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Scan</p>
                <div className="aspect-square rounded-lg overflow-hidden bg-black relative">
                  <img
                    src={result.scan_image_url}
                    alt="Scan"
                    className="w-full h-full object-cover cursor-zoom-in"
                    onClick={() => setLightbox({ images: [result.scan_image_url!], index: 0 })}
                  />
                  <div className="absolute bottom-2 right-2 bg-black/60 text-white p-1 rounded-full pointer-events-none">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="7" />
                      <line x1="21" y1="21" x2="16.65" y2="16.65" />
                      <line x1="11" y1="8" x2="11" y2="14" />
                      <line x1="8" y1="11" x2="14" y2="11" />
                    </svg>
                  </div>
                </div>
              </div>
            )}
            {referenceImages.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Referentie</p>
                <ImageSlideshow
                  images={referenceImages}
                  maxWidth="100%"
                  onImageClick={(i) => setLightbox({ images: referenceImages, index: i })}
                />
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={onNext}>
          Opnieuw scannen
        </Button>
        <Button variant="secondary" className="w-full" onClick={onDone}>
          Terug naar orders
        </Button>
      </div>

      <ImageLightbox
        images={lightbox?.images ?? []}
        startIndex={lightbox?.index ?? 0}
        open={lightbox !== null}
        onClose={() => setLightbox(null)}
      />
    </>
  );
}
