import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BottleSkuCombobox } from "./skus";

afterEach(() => cleanup());

const options = [
  {
    id: 1,
    sku_code: "FLES-001",
    name: "Eerste wijn",
    is_bottle: true,
    producent: "Producent Noord",
    supplier_name: "Leverancier Een",
  },
  {
    id: 2,
    sku_code: "FLES-002",
    name: "Tweede wijn",
    is_bottle: true,
    producent: "Producent Zuid",
    supplier_name: "Leverancier Twee",
  },
];

describe("fles koppelen aan een doos", () => {
  it("zoekt in naam, SKU-code, producent en leverancier", () => {
    render(
      <BottleSkuCombobox
        options={options}
        value={null}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /geen fles gekoppeld/i }));
    fireEvent.change(screen.getByLabelText("Fles zoeken"), {
      target: { value: "zuid" },
    });

    expect(screen.getByRole("option", { name: /Tweede wijn/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Eerste wijn/ })).toBeNull();
  });

  it("geeft de gekozen fles door en sluit de lijst", () => {
    const onChange = vi.fn();
    render(
      <BottleSkuCombobox
        options={options}
        value={null}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /geen fles gekoppeld/i }));
    fireEvent.click(screen.getByRole("option", { name: /Tweede wijn/ }));

    expect(onChange).toHaveBeenCalledWith(2);
    expect(screen.queryByLabelText("Fles zoeken")).toBeNull();
  });
});
