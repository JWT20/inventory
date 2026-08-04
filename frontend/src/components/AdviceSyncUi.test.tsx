import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UnmatchedProductActions } from "./inbound";
import { adviceSyncConflictMessage } from "@/lib/api";

afterEach(() => cleanup());

describe("advice product sync UI", () => {
  it("keeps local bottle concepts available for organizations without the feed", () => {
    const onCreateConcept = vi.fn();

    render(
      <UnmatchedProductActions
        adviceSyncAvailable={false}
        syncingAdvice={false}
        onCreateConcept={onCreateConcept}
        onSyncAdvice={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Synchroniseer nu" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Losse fles" }));
    expect(onCreateConcept).toHaveBeenCalledWith(true);
  });

  it("replaces local bottle creation with sync for the advice organization", () => {
    const onSyncAdvice = vi.fn();

    render(
      <UnmatchedProductActions
        adviceSyncAvailable
        syncingAdvice={false}
        onCreateConcept={vi.fn()}
        onSyncAdvice={onSyncAdvice}
      />,
    );

    expect(screen.queryByRole("button", { name: "Losse fles" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Synchroniseer nu" }));
    expect(onSyncAdvice).toHaveBeenCalledOnce();
  });

  it("surfaces the first catalog conflict to the operator", () => {
    const message = adviceSyncConflictMessage({
      received: 2,
      created: 1,
      updated: 0,
      deactivated: 0,
      conflicts: ["prd-2: SKU-code TEST-FLES bestaat al"],
    });

    expect(message).toContain("1 conflict");
    expect(message).toContain("TEST-FLES bestaat al");
  });
});
