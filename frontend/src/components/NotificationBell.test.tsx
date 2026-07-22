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
});
