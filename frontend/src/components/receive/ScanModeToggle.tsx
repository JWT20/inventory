import type { ScanMode } from "./types";

/** Toggle between scanning boxes and loose bottles — selects the match pool. */
export function ScanModeToggle({
  mode,
  onChange,
}: {
  mode: ScanMode;
  onChange: (mode: ScanMode) => void;
}) {
  return (
    <div className="flex rounded-md border border-border overflow-hidden mb-3 text-sm">
      <button
        type="button"
        onClick={() => onChange("box")}
        className={`flex-1 px-3 py-2 transition-colors ${
          mode === "box"
            ? "bg-primary text-primary-foreground"
            : "bg-background hover:bg-muted"
        }`}
      >
        Doos
      </button>
      <button
        type="button"
        onClick={() => onChange("bottle")}
        className={`flex-1 px-3 py-2 transition-colors border-l border-border ${
          mode === "bottle"
            ? "bg-primary text-primary-foreground"
            : "bg-background hover:bg-muted"
        }`}
      >
        Fles
      </button>
    </div>
  );
}
