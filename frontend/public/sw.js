self.addEventListener("push", (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = {};
  }
  event.waitUntil(
    self.registration.showNotification(message.title || "Nieuwe melding", {
      body: message.body || "Er staat een nieuwe order klaar.",
      icon: "/app-icon.svg",
      badge: "/app-icon.svg",
      tag: message.tag,
      data: { url: message.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (windows) => {
      const sameOriginWindow = windows.find(
        (windowClient) => new URL(windowClient.url).origin === self.location.origin,
      );
      if (sameOriginWindow) {
        await sameOriginWindow.navigate(targetUrl);
        return sameOriginWindow.focus();
      }
      return clients.openWindow(targetUrl);
    }),
  );
});
