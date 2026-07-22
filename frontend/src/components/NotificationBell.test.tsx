import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationBell } from "./notification-bell";

const mocks = vi.hoisted(() => ({
  pushConfig: vi.fn(),
  pushSubscribe: vi.fn(),
  pushUnsubscribe: vi.fn(),
  requestPermission: vi.fn(),
  register: vi.fn(),
  getSubscription: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  toastInfo: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

const currentUser = {
  id: 7,
  username: "koerier",
  role: "courier",
  is_platform_admin: false,
};

vi.mock("@/lib/api", () => ({
  api: {
    pushConfig: mocks.pushConfig,
    pushSubscribe: mocks.pushSubscribe,
    pushUnsubscribe: mocks.pushUnsubscribe,
  },
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: currentUser }),
}));
vi.mock("sonner", () => ({
  toast: {
    info: mocks.toastInfo,
    error: mocks.toastError,
    success: mocks.toastSuccess,
  },
}));

const subscription = {
  endpoint: "https://push.example/browser",
  toJSON: () => ({
    endpoint: "https://push.example/browser",
    keys: { p256dh: "browser-key", auth: "browser-auth" },
  }),
  unsubscribe: mocks.unsubscribe,
} as unknown as PushSubscription;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.pushConfig.mockResolvedValue({ enabled: true, public_key: "AQID" });
  mocks.pushSubscribe.mockResolvedValue(null);
  mocks.pushUnsubscribe.mockResolvedValue(null);
  mocks.requestPermission.mockResolvedValue("granted");
  mocks.getSubscription.mockResolvedValue(null);
  mocks.subscribe.mockResolvedValue(subscription);
  mocks.unsubscribe.mockResolvedValue(true);

  const registration = {
    pushManager: {
      getSubscription: mocks.getSubscription,
      subscribe: mocks.subscribe,
    },
  } as unknown as ServiceWorkerRegistration;
  mocks.register.mockResolvedValue(registration);
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { register: mocks.register, ready: Promise.resolve(registration) },
  });
  Object.defineProperty(window, "PushManager", {
    configurable: true,
    value: function PushManager() {},
  });
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: { permission: "default", requestPermission: mocks.requestPermission },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({ matches: false }),
  });
  Object.defineProperty(navigator, "userAgent", {
    configurable: true,
    value: "Mozilla/5.0 test browser",
  });
  Object.defineProperty(navigator, "platform", {
    configurable: true,
    value: "Test",
  });
  Object.defineProperty(navigator, "maxTouchPoints", {
    configurable: true,
    value: 0,
  });
  Object.defineProperty(navigator, "standalone", {
    configurable: true,
    value: false,
  });
});

afterEach(() => cleanup());

describe("NotificationBell", () => {
  it("subscribes from the compact off-state bell", async () => {
    render(<NotificationBell />);

    const button = await screen.findByRole("button", {
      name: "Pushmeldingen inschakelen",
    });
    fireEvent.click(button);

    await waitFor(() => expect(mocks.pushSubscribe).toHaveBeenCalledWith({
      endpoint: "https://push.example/browser",
      keys: { p256dh: "browser-key", auth: "browser-auth" },
    }));
    expect(
      await screen.findByRole("button", { name: "Pushmeldingen uitschakelen" }),
    ).toBeTruthy();
  });

  it("rebinds and can disable an existing subscription", async () => {
    mocks.getSubscription.mockResolvedValue(subscription);
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: { permission: "granted", requestPermission: mocks.requestPermission },
    });
    render(<NotificationBell />);

    const button = await screen.findByRole("button", {
      name: "Pushmeldingen uitschakelen",
    });
    expect(mocks.pushSubscribe).toHaveBeenCalledTimes(1);
    fireEvent.click(button);

    await waitFor(() =>
      expect(mocks.pushUnsubscribe).toHaveBeenCalledWith(subscription.endpoint),
    );
    expect(mocks.unsubscribe).toHaveBeenCalled();
    expect(
      await screen.findByRole("button", { name: "Pushmeldingen inschakelen" }),
    ).toBeTruthy();
  });

  it("keeps the bell visible when push is not configured", async () => {
    mocks.pushConfig.mockResolvedValue({ enabled: false, public_key: "" });
    render(<NotificationBell />);

    const button = await screen.findByRole("button", {
      name: "Pushmeldingen inschakelen",
    });
    fireEvent.click(button);

    expect(mocks.toastInfo).toHaveBeenCalledWith(
      "Pushmeldingen zijn nog niet geconfigureerd.",
    );
  });

  it("keeps the bell visible when loading the config fails", async () => {
    mocks.pushConfig.mockRejectedValue(new Error("offline"));
    render(<NotificationBell />);

    const button = await screen.findByRole("button", {
      name: "Pushmeldingen inschakelen",
    });
    fireEvent.click(button);

    expect(mocks.toastError).toHaveBeenCalledWith(
      "Pushmeldingen kunnen niet worden geladen. Ververs de app en probeer opnieuw.",
    );
  });

  it("tells iPhone Safari users to install the app first", async () => {
    Object.defineProperty(navigator, "userAgent", {
      configurable: true,
      value: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
    });
    render(<NotificationBell />);

    const button = await screen.findByRole("button", {
      name: "Pushmeldingen inschakelen",
    });
    fireEvent.click(button);

    expect(mocks.pushConfig).not.toHaveBeenCalled();
    expect(mocks.toastInfo).toHaveBeenCalledWith(
      "Voeg de app toe aan je beginscherm om pushmeldingen te ontvangen.",
    );
  });

  it("loads push normally in an installed iPhone app", async () => {
    Object.defineProperty(navigator, "userAgent", {
      configurable: true,
      value: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
    });
    Object.defineProperty(navigator, "standalone", {
      configurable: true,
      value: true,
    });
    render(<NotificationBell />);

    expect(await screen.findByRole("button", {
      name: "Pushmeldingen inschakelen",
    })).toBeTruthy();
    expect(mocks.pushConfig).toHaveBeenCalledTimes(1);
    expect(mocks.toastInfo).not.toHaveBeenCalled();
  });

  it("explains how to recover denied browser permission", async () => {
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: { permission: "denied", requestPermission: mocks.requestPermission },
    });
    render(<NotificationBell />);

    const button = await screen.findByRole("button", {
      name: "Pushmeldingen inschakelen",
    });
    fireEvent.click(button);

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith(
      "Meldingen zijn geblokkeerd. Sta ze toe via de instellingen van deze website in je browser.",
    ));
    expect(mocks.requestPermission).not.toHaveBeenCalled();
  });
});
