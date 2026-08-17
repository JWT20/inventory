import { describe, expect, it } from "vitest";

import { addressLines, type DeliveryAddress } from "./channels";

const address: DeliveryAddress = {
  recipient_name: "Anna de Vries",
  street: "Turfsingel",
  house_number: "8",
  house_number_suffix: null,
  postal_code: "9712 KR",
  city: "Groningen",
  country: "NL",
  phone: null,
};

describe("delivery address on the Kanalen page", () => {
  it("omits the country for a domestic parcel", () => {
    expect(addressLines(address)).toEqual([
      "Anna de Vries",
      "Turfsingel 8",
      "9712 KR Groningen",
    ]);
  });

  it("names the country once the parcel leaves the Netherlands", () => {
    expect(addressLines({ ...address, country: "be" })).toEqual([
      "Anna de Vries",
      "Turfsingel 8",
      "9712 KR Groningen",
      "BE",
    ]);
  });

  it("keeps the house number suffix a separate word", () => {
    expect(addressLines({ ...address, house_number_suffix: "B" })[1]).toBe(
      "Turfsingel 8 B",
    );
  });
});
