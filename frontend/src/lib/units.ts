// Bottle SKUs (is_bottle) are their own order unit: 1 fles = 1 scan = 1 boeking.
// Box and bottle counts are never mixed; these helpers label them separately.

export const unitLabel = (isBottle: boolean | undefined, n: number): string =>
  isBottle ? (n === 1 ? "fles" : "flessen") : n === 1 ? "doos" : "dozen";

/** "3 dozen · 2 flessen" — omits the bottle part when there are none. */
export const formatBoxesBottles = (boxes: number, bottles: number): string => {
  const parts: string[] = [];
  if (boxes > 0 || bottles === 0) parts.push(`${boxes} ${unitLabel(false, boxes)}`);
  if (bottles > 0) parts.push(`${bottles} ${unitLabel(true, bottles)}`);
  return parts.join(" · ");
};

/** "2/3 dozen · 1/2 flessen" — booked/total per unit, bottles only when present. */
export const formatBookedBoxesBottles = (
  bookedBoxes: number,
  totalBoxes: number,
  bookedBottles: number,
  totalBottles: number,
): string => {
  const parts: string[] = [];
  if (totalBoxes > 0 || totalBottles === 0) {
    parts.push(`${bookedBoxes}/${totalBoxes} dozen`);
  }
  if (totalBottles > 0) parts.push(`${bookedBottles}/${totalBottles} flessen`);
  return parts.join(" · ");
};
