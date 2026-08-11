import { useState } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ImageSlideshow } from "@/components/image-slideshow";
import { ImageLightbox } from "@/components/image-lightbox";
import { QuantityPicker } from "@/components/quantity-picker";
import { SCAN_MODE_WORD, SCAN_MODE_WORD_PLURAL } from "./constants";
import type { BookingResult, ConfirmationData, ScanMode } from "./types";

export function ConfirmStep({
  confirmation,
  scanMode,
  onConfirmed,
  onReject,
}: {
  confirmation: ConfirmationData;
  scanMode: ScanMode;
  onConfirmed: (booking: BookingResult) => void;
  onReject: () => void;
}) {
  const unitWord = SCAN_MODE_WORD[scanMode];
  const unitWordPlural = SCAN_MODE_WORD_PLURAL[scanMode];
  const [confirming, setConfirming] = useState(false);
  const [quantity, setQuantity] = useState(1);
  const [lightbox, setLightbox] = useState<{ images: string[]; index: number } | null>(null);
  const hasAlternatives = confirmation.alternatives && confirmation.alternatives.length > 0;
  const capRemaining = confirmation.cap_for_customer != null
    ? confirmation.cap_for_customer
    : (confirmation.remaining_quantity ?? 1);
  const maxQuantity = Math.min(confirmation.remaining_quantity ?? 1, capRemaining);
  const hasCap = confirmation.cap_for_customer != null
    && confirmation.ordered_by_customer != null
    && confirmation.cap_for_customer < confirmation.ordered_by_customer;
  const manualReviewRequired = confirmation.manual_review_required === true;
  const highConfidence = !hasAlternatives
    && !manualReviewRequired
    && confirmation.confidence >= 0.84;

  async function handleConfirm(token?: string) {
    setConfirming(true);
    try {
      const booking: BookingResult = await api.confirmBooking(
        token ?? confirmation.confirmation_token,
        quantity,
      );
      onConfirmed(booking);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Bevestiging mislukt");
    } finally {
      setConfirming(false);
    }
  }

  return (
    <>
      {hasAlternatives ? (
        <div className="p-4 rounded-lg bg-orange-600/20 border-2 border-orange-600 text-center mb-4">
          <p className="text-orange-400 text-xl font-bold mb-1">
            Meerdere matches
          </p>
          <p className="text-orange-300 text-sm">
            Vergelijkbare producten gevonden — welke {unitWord} is dit?
          </p>
        </div>
      ) : highConfidence ? (
        <div className="p-4 rounded-lg bg-green-600/20 border-2 border-green-600 text-center mb-4">
          <p className="text-green-400 text-xl font-bold mb-1">
            Match gevonden
          </p>
          <p className="text-green-300 text-sm">
            {Math.round(confirmation.confidence * 100)}% zekerheid — bevestig om te boeken
          </p>
        </div>
      ) : (
        <div className="p-4 rounded-lg bg-yellow-600/20 border-2 border-yellow-600 text-center mb-4">
          <p className="text-yellow-400 text-xl font-bold mb-1">
            {manualReviewRequired ? "Handmatige controle nodig" : "Controleer match"}
          </p>
          <p className="text-yellow-300 text-sm">
            {manualReviewRequired
              ? `Waarschijnlijk ${confirmation.sku_name} — vergelijk de foto's`
              : "Onzekere match — bevestig handmatig"}
          </p>
        </div>
      )}

      {/* Why this scan is being questioned — the visual check's own words when
          it found one, so the picker knows what to look at on the box. */}
      {confirmation.confirmation_reason && (
        <div className="p-3 rounded-lg bg-muted/50 border mb-4">
          <p className="text-xs text-muted-foreground">
            {confirmation.confirmation_reason}
          </p>
        </div>
      )}

      {/* Rolcontainer assignment */}
      {confirmation.rolcontainer && (
        <div className="p-4 rounded-lg bg-muted/50 border text-center mb-4">
          <p className="text-sm text-muted-foreground mb-1">Zet op rolcontainer</p>
          <p className="text-xl font-black">{confirmation.rolcontainer}</p>
        </div>
      )}

      {/* Scan image */}
      <Card className="p-4 mb-4">
        <p className="text-xs text-muted-foreground mb-2 font-semibold">Uw scan</p>
        <div className="aspect-square rounded-lg overflow-hidden bg-black max-w-[200px] mx-auto relative">
          <img
            src={confirmation.scan_image_url}
            alt="Scan"
            className="w-full h-full object-cover cursor-zoom-in"
            onClick={() => setLightbox({ images: [confirmation.scan_image_url], index: 0 })}
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
      </Card>

      {/* Quantity picker */}
      {maxQuantity > 1 && (
        <Card className="p-4 mb-4">
          <p className="text-xs text-muted-foreground mb-2 font-semibold text-center">
            Hoeveel {unitWordPlural} van dit product?
          </p>
          <QuantityPicker value={quantity} onChange={setQuantity} max={maxQuantity} />
          <p className="text-xs text-muted-foreground mt-2 text-center">
            {maxQuantity} over in deze order
          </p>
          {hasCap && (
            <p className="text-xs text-orange-400 mt-2 text-center">
              Max voor {confirmation.klant} vandaag: {confirmation.cap_for_customer} van {confirmation.ordered_by_customer} {unitWordPlural} {confirmation.sku_name}
            </p>
          )}
        </Card>
      )}
      {maxQuantity <= 1 && hasCap && (
        <Card className="p-4 mb-4">
          <p className="text-xs text-orange-400 text-center">
            Max voor {confirmation.klant} vandaag: {confirmation.cap_for_customer} van {confirmation.ordered_by_customer} {unitWordPlural} {confirmation.sku_name}
          </p>
        </Card>
      )}

      {hasAlternatives ? (
        <>
          {/* Best match */}
          <Card className="p-4 mb-3 border-2 border-green-600/50">
            <div className="space-y-1 mb-3">
              <p className="text-sm font-semibold text-green-400">Beste match</p>
              <p className="text-sm">
                <span className="text-muted-foreground">Product:</span>{" "}
                <span className="font-semibold">{confirmation.sku_name}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">SKU:</span>{" "}
                <span className="font-mono">{confirmation.sku_code}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">Zekerheid:</span>{" "}
                {Math.round(confirmation.confidence * 100)}%
              </p>
            </div>
            <ImageSlideshow
              images={confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : [])}
              maxWidth="160px"
              onImageClick={(i) => {
                const imgs = confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : []);
                setLightbox({ images: imgs, index: i });
              }}
            />
            <Button
              size="lg"
              className="w-full h-12 text-base bg-green-600 hover:bg-green-700 mt-3"
              onClick={() => handleConfirm()}
              disabled={confirming}
            >
              {confirming ? "Boeken..." : `Dit is ${confirmation.sku_name}${quantity > 1 ? ` (${quantity}×)` : ""}`}
            </Button>
          </Card>

          {/* Alternatives. A lookalike that is not open in this scope carries
              bookable=false: it gets a photo and a reason but no confirm
              button, because it is a warning ("this may be the box you are
              holding"), not something that can be booked here. */}
          {confirmation.alternatives!.map((alt) => {
            const bookable = alt.bookable !== false && !!alt.confirmation_token;
            return (
              <Card
                key={alt.sku_id}
                className={
                  bookable
                    ? "p-4 mb-3 border border-muted"
                    : "p-4 mb-3 border border-orange-600/50 bg-orange-950/20"
                }
              >
                <div className="space-y-1 mb-3">
                  {!bookable && (
                    <p className="text-sm font-semibold text-orange-400">
                      Lijkt hier ook op — niet te boeken
                    </p>
                  )}
                  <p className="text-sm">
                    <span className="text-muted-foreground">Product:</span>{" "}
                    <span className="font-semibold">{alt.sku_name}</span>
                  </p>
                  <p className="text-sm">
                    <span className="text-muted-foreground">SKU:</span>{" "}
                    <span className="font-mono">{alt.sku_code}</span>
                  </p>
                  <p className="text-sm">
                    <span className="text-muted-foreground">Zekerheid:</span>{" "}
                    {Math.round(alt.confidence * 100)}%
                  </p>
                  {alt.note && (
                    <p className="text-xs text-orange-400">{alt.note}</p>
                  )}
                </div>
                <ImageSlideshow
                  images={alt.reference_image_urls?.length ? alt.reference_image_urls : (alt.reference_image_url ? [alt.reference_image_url] : [])}
                  maxWidth="160px"
                  onImageClick={(i) => {
                    const imgs = alt.reference_image_urls?.length ? alt.reference_image_urls : (alt.reference_image_url ? [alt.reference_image_url] : []);
                    setLightbox({ images: imgs, index: i });
                  }}
                />
                {bookable && (
                  <Button
                    size="lg"
                    className="w-full h-12 text-base mt-3"
                    variant="outline"
                    onClick={() => handleConfirm(alt.confirmation_token)}
                    disabled={confirming}
                  >
                    {confirming ? "Boeken..." : `Dit is ${alt.sku_name}`}
                  </Button>
                )}
              </Card>
            );
          })}

          <Button
            variant="destructive"
            size="lg"
            className="w-full h-14 text-lg mt-2"
            onClick={onReject}
            disabled={confirming}
          >
            Geen van deze — opnieuw scannen
          </Button>
        </>
      ) : (
        <>
          <Card className="p-4 mb-4">
            <div className="space-y-1 mb-3">
              <p className="text-sm">
                <span className="text-muted-foreground">Product:</span>{" "}
                <span className="font-semibold">{confirmation.sku_name}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">SKU:</span>{" "}
                <span className="font-mono">{confirmation.sku_code}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">Zekerheid:</span>{" "}
                {Math.round(confirmation.confidence * 100)}%
              </p>
              {confirmation.klant && (
                <p className="text-sm">
                  <span className="text-muted-foreground">Klant:</span>{" "}
                  {confirmation.klant}
                </p>
              )}
            </div>

            <p className="text-xs text-muted-foreground mb-2 font-semibold">
              Is dit dezelfde {unitWord}?
            </p>
            <ImageSlideshow
              images={confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : [])}
              maxWidth="200px"
              onImageClick={(i) => {
                const imgs = confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : []);
                setLightbox({ images: imgs, index: i });
              }}
            />
          </Card>

          <div className="flex flex-col gap-3">
            <Button
              size="lg"
              className="w-full h-14 text-lg bg-green-600 hover:bg-green-700"
              onClick={() => handleConfirm()}
              disabled={confirming}
            >
              {confirming
                ? "Boeken..."
                : manualReviewRequired
                  ? `Ja, dit is ${confirmation.sku_name}${quantity > 1 ? ` (${quantity}×)` : ""}`
                  : `Ja, dit klopt${quantity > 1 ? ` (${quantity}×)` : ""}`}
            </Button>
            <Button
              variant="destructive"
              size="lg"
              className="w-full h-14 text-lg"
              onClick={onReject}
              disabled={confirming}
            >
              Nee, opnieuw scannen
            </Button>
          </div>
        </>
      )}

      <ImageLightbox
        images={lightbox?.images ?? []}
        startIndex={lightbox?.index ?? 0}
        open={lightbox !== null}
        onClose={() => setLightbox(null)}
      />
    </>
  );
}
