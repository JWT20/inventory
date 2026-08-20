(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

// jsdom has no Pointer Events and no scrollIntoView. Radix' Select uses both
// the moment it opens, so without these a test can render a dropdown but never
// click one. Defined only where missing, so a real implementation always wins.
for (const method of [
  "hasPointerCapture",
  "setPointerCapture",
  "releasePointerCapture",
  "scrollIntoView",
] as const) {
  if (!(method in Element.prototype)) {
    Object.defineProperty(Element.prototype, method, {
      configurable: true,
      value: () => false,
    });
  }
}
