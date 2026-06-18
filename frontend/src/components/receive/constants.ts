import type { ScanMode } from "./types";

export const SCAN_MODE_WORD: Record<ScanMode, string> = { box: "doos", bottle: "fles" };
export const SCAN_MODE_WORD_PLURAL: Record<ScanMode, string> = { box: "dozen", bottle: "flessen" };

export const DELIVERY_DAY_LABELS: Record<string, string> = {
  monday: "maandag",
  tuesday: "dinsdag",
  wednesday: "woensdag",
  thursday: "donderdag",
  friday: "vrijdag",
};

export const DELIVERY_DAY_SHORT: Record<string, string> = {
  monday: "ma",
  tuesday: "di",
  wednesday: "wo",
  thursday: "do",
  friday: "vr",
};
