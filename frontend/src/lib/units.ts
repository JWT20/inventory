// Handling units stay separate: wine uses boxes/bottles, EAN products use items.

export const unitLabel = (
  isBottle: boolean | undefined,
  n: number,
  isItem = false,
): string =>
  isItem
    ? n === 1
      ? "item"
      : "items"
    : isBottle
      ? n === 1
        ? "fles"
        : "flessen"
      : n === 1
        ? "doos"
        : "dozen";

/** "3 dozen · 2 flessen · 4 items" — omits empty unit types. */
export const formatBoxesBottles = (
  boxes: number,
  bottles: number,
  items = 0,
): string => {
  const parts: string[] = [];
  if (boxes > 0 || (bottles === 0 && items === 0)) {
    parts.push(`${boxes} ${unitLabel(false, boxes)}`);
  }
  if (bottles > 0) parts.push(`${bottles} ${unitLabel(true, bottles)}`);
  if (items > 0) parts.push(`${items} ${unitLabel(false, items, true)}`);
  return parts.join(" · ");
};

/** "2/3 dozen · 1/2 flessen · 3/4 items" — booked/total per unit. */
export const formatBookedBoxesBottles = (
  bookedBoxes: number,
  totalBoxes: number,
  bookedBottles: number,
  totalBottles: number,
  bookedItems = 0,
  totalItems = 0,
): string => {
  const parts: string[] = [];
  if (totalBoxes > 0 || (totalBottles === 0 && totalItems === 0)) {
    parts.push(`${bookedBoxes}/${totalBoxes} dozen`);
  }
  if (totalBottles > 0) parts.push(`${bookedBottles}/${totalBottles} flessen`);
  if (totalItems > 0) parts.push(`${bookedItems}/${totalItems} items`);
  return parts.join(" · ");
};
