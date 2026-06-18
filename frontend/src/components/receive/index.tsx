import { useState } from "react";
import { OrderSelectStep } from "./OrderSelectStep";
import { ThisWeekStep } from "./ThisWeekStep";
import { ScanStep } from "./ScanStep";
import { ConfirmStep } from "./ConfirmStep";
import { ResultStep } from "./ResultStep";
import { IdentifyScanStep } from "./IdentifyScanStep";
import { IdentifyResultStep } from "./IdentifyResultStep";
import { getISOWeek } from "./week";
import type {
  BookingResult,
  ConfirmationData,
  IdentifyResult,
  Order,
  ScanMode,
} from "./types";

type Step = "select-order" | "this-week" | "scan" | "result" | "confirm" | "identify-scan" | "identify-result";

export function ReceivePage() {
  const [step, setStep] = useState<Step>("select-order");
  const [scanMode, setScanMode] = useState<ScanMode>("box");
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [overviewWeek, setOverviewWeek] = useState(() => getISOWeek(new Date()));
  const [lastBooking, setLastBooking] = useState<BookingResult | null>(null);
  const [lastIdentify, setLastIdentify] = useState<IdentifyResult | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<ConfirmationData | null>(null);

  function handleOrderSelected(order: Order) {
    setSelectedOrder(order);
    setStep("scan");
  }

  function handleThisWeek(week: string) {
    setOverviewWeek(week);
    setStep("this-week");
  }

  function handleBooked(booking: ConfirmationData) {
    setPendingConfirmation(booking);
    setStep("confirm");
  }

  function handleIdentified(result: IdentifyResult) {
    setLastIdentify(result);
    setStep("identify-result");
  }

  function handleConfirmed(booking: BookingResult) {
    setPendingConfirmation(null);
    setLastBooking(booking);
    setStep("result");
  }

  function scanNext() {
    setLastBooking(null);
    setPendingConfirmation(null);
    setStep("scan");
  }

  function reset() {
    setStep("select-order");
    setSelectedOrder(null);
    setLastBooking(null);
    setLastIdentify(null);
    setPendingConfirmation(null);
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Scan & Boek</h2>

      {step === "select-order" && (
        <OrderSelectStep
          onSelect={handleOrderSelected}
          onIdentify={() => setStep("identify-scan")}
          onThisWeek={handleThisWeek}
        />
      )}

      {step === "this-week" && (
        <ThisWeekStep week={overviewWeek} onBack={reset} />
      )}

      {step === "scan" && selectedOrder && (
        <ScanStep
          order={selectedOrder}
          scanMode={scanMode}
          onScanModeChange={setScanMode}
          onBooked={handleBooked}
          onBack={reset}
        />
      )}

      {step === "confirm" && pendingConfirmation && selectedOrder && (
        <ConfirmStep
          confirmation={pendingConfirmation}
          scanMode={scanMode}
          onConfirmed={handleConfirmed}
          onReject={scanNext}
        />
      )}

      {step === "result" && lastBooking && selectedOrder && (
        <ResultStep
          booking={lastBooking}
          order={selectedOrder}
          scanMode={scanMode}
          onNext={scanNext}
          onDone={reset}
        />
      )}

      {step === "identify-scan" && (
        <IdentifyScanStep
          scanMode={scanMode}
          onScanModeChange={setScanMode}
          onIdentified={handleIdentified}
          onBack={reset}
        />
      )}

      {step === "identify-result" && (
        <IdentifyResultStep
          result={lastIdentify}
          onNext={() => { setLastIdentify(null); setStep("identify-scan"); }}
          onDone={reset}
        />
      )}
    </div>
  );
}
