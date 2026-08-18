import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SupplierPicker, supplierGateMessage } from "./inbound";

afterEach(() => cleanup());

describe("inbound supplier selection", () => {
  it("blocks extraction until a supplier from the list is chosen", () => {
    expect(supplierGateMessage("", { picker: true })).toMatch(/Kies eerst een leverancier/);
    expect(supplierGateMessage("   ", { picker: true })).not.toBeNull();
    expect(supplierGateMessage("Anfors-Imperial", { picker: true })).toBeNull();
  });

  it("keeps the free-text flow open for organizations without a supplier list", () => {
    expect(supplierGateMessage("", { picker: false })).toBeNull();
  });

  it("falls back to a text field when no suppliers are known", () => {
    const onChange = vi.fn();
    render(<SupplierPicker suppliers={[]} value="" onChange={onChange} />);

    const input = screen.getByPlaceholderText("Leverancier (optioneel)");
    fireEvent.change(input, { target: { value: "Nieuwe leverancier" } });
    expect(onChange).toHaveBeenCalledWith("Nieuwe leverancier");
  });

  it("offers the merchant's own supplier names once the list is known", () => {
    render(
      <SupplierPicker
        suppliers={["Adbibendum", "Anfors-Imperial"]}
        value="Anfors-Imperial"
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByPlaceholderText("Leverancier (optioneel)")).toBeNull();
    expect(screen.getByLabelText("Leverancier").textContent).toContain("Anfors-Imperial");
  });
});
