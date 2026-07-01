import { useEffect, useState } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { fireCompletion } from "@/lib/celebrate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ImageSlideshow } from "@/components/image-slideshow";
import { ImageLightbox } from "@/components/image-lightbox";
import { QuantityPicker } from "@/components/quantity-picker";
import { DistributionPanel } from "./DistributionPanel";
import { SCAN_MODE_WORD } from "./constants";
import type { BookingResult, Order, ScanMode } from "./types";

export function ResultStep({
  booking,
  order,
  scanMode,
  onNext,
  onDone,
}: {
  booking: BookingResult;
  order: Order;
  scanMode: ScanMode;
  onNext: () => void;
  onDone: () => void;
}) {
  const referenceImages = booking.reference_image_urls ?? [];
  const [remaining, setRemaining] = useState(booking.remaining_quantity ?? 0);
  const [moreQuantity, setMoreQuantity] = useState(1);
  const [bookingMore, setBookingMore] = useState(false);
  const [totalBooked, setTotalBooked] = useState(booking.booked_quantity ?? 1);
  const [lightbox, setLightbox] = useState<{ images: string[]; index: number } | null>(null);

  // Celebrate when this booking was the one that completed the order.
  useEffect(() => {
    if (booking.order_completed) fireCompletion();
  }, [booking.order_completed]);

  async function handleBookMore() {
    if (!booking.order_line_id) return;
    setBookingMore(true);
    try {
      const result: BookingResult = await api.bookMore(
        booking.order_line_id,
        moreQuantity,
        booking.scan_image_url ?? "",
      );
      const actualBooked = result.booked_quantity ?? moreQuantity;
      setTotalBooked((prev) => prev + actualBooked);
      setRemaining(result.remaining_quantity ?? 0);
      setMoreQuantity(1);
      toast.success(`${actualBooked}× extra geboekt`);
      if (result.order_completed) fireCompletion();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Boeken mislukt";
      if (msg.includes("allocation_cap_reached") || msg.includes("Toewijzingslimiet")) {
        toast.error("Toewijzingslimiet bereikt — niet meer beschikbaar voor deze klant vandaag");
      } else {
        toast.error(msg);
      }
    } finally {
      setBookingMore(false);
    }
  }

  return (
    <>
      <div className="p-6 rounded-lg bg-green-600/20 border-2 border-green-600 text-center mb-4">
        <p className="text-green-400 text-2xl font-bold mb-2">
          Zet op rolcontainer
        </p>
        <p className="text-green-300 text-3xl font-black">
          {booking.rolcontainer}
        </p>
        {totalBooked > 1 && (
          <p className="text-green-400 text-lg mt-1">
            {totalBooked}× geboekt
          </p>
        )}
      </div>

      <Card className="p-4 mb-4">
        <div className="space-y-1 mb-3">
          <p className="text-sm">
            <span className="text-muted-foreground">Product:</span>{" "}
            <span className="font-semibold">{booking.sku_name}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">SKU:</span>{" "}
            <span className="font-mono">{booking.sku_code}</span>
          </p>
          {booking.confidence != null && booking.confidence > 0 && (
            <p className="text-sm">
              <span className="text-muted-foreground">Zekerheid:</span>{" "}
              {Math.round(booking.confidence * 100)}%
            </p>
          )}
          <p className="text-sm">
            <span className="text-muted-foreground">Order:</span>{" "}
            {booking.order_reference}
          </p>
          {booking.context_order_reference && booking.context_order_reference !== booking.order_reference && (
            <p className="text-sm">
              <span className="text-muted-foreground">Gestart vanuit:</span>{" "}
              {booking.context_order_reference}
            </p>
          )}
          <p className="text-sm">
            <span className="text-muted-foreground">Klant:</span>{" "}
            {booking.klant}
          </p>
        </div>

        {/* Scan vs referentie vergelijking */}
        {(booking.scan_image_url || referenceImages.length > 0) && (
          <div className="grid grid-cols-2 gap-3">
            {booking.scan_image_url && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Scan</p>
                <div className="aspect-square rounded-lg overflow-hidden bg-black relative">
                  <img
                    src={booking.scan_image_url}
                    alt="Scan"
                    className="w-full h-full object-cover cursor-zoom-in"
                    onClick={() => setLightbox({ images: [booking.scan_image_url!], index: 0 })}
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

      {/* Book more identical boxes */}
      {remaining > 0 && booking.order_line_id && (
        <Card className="p-4 mb-4 border-2 border-blue-600/30">
          <p className="text-sm font-semibold text-center mb-3">
            Nog {remaining} dezelfde in deze order
          </p>
          <QuantityPicker
            value={moreQuantity}
            onChange={setMoreQuantity}
            max={remaining}
          />
          <Button
            size="lg"
            className="w-full h-12 text-base mt-3"
            onClick={handleBookMore}
            disabled={bookingMore}
          >
            {bookingMore ? "Boeken..." : `${moreQuantity}× extra boeken`}
          </Button>
        </Card>
      )}

      {/* Read-only verdeel-lijst: which other customers this SKU still needs to go to */}
      {booking.sku_id != null && (
        <DistributionPanel orderId={order.id} skuId={booking.sku_id} refreshKey={totalBooked} />
      )}

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={onNext}>
          Volgende {SCAN_MODE_WORD[scanMode]} scannen
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
