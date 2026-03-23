interface QuantityPickerProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max: number;
}

export function QuantityPicker({
  value,
  onChange,
  min = 1,
  max,
}: QuantityPickerProps) {
  return (
    <div className="flex items-center justify-center gap-3">
      <button
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        className="w-12 h-12 rounded-full bg-muted text-foreground text-2xl font-bold flex items-center justify-center disabled:opacity-30 active:scale-95 transition-transform"
        aria-label="Minder"
      >
        &minus;
      </button>
      <span className="text-3xl font-black w-16 text-center tabular-nums">
        {value}
      </span>
      <button
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        className="w-12 h-12 rounded-full bg-muted text-foreground text-2xl font-bold flex items-center justify-center disabled:opacity-30 active:scale-95 transition-transform"
        aria-label="Meer"
      >
        +
      </button>
    </div>
  );
}
