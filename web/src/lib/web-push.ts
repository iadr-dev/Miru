import { registerDevice, removeDevice } from "@/lib/api/settings";

/** Browser notifications (Web Push). The server sends an encrypted message to a per-browser
 * subscription; a service worker (`/sw.js`) shows it. Nothing here runs until the user presses the
 * button — the permission prompt is never shown on page load. */

export type WebPushState = "unsupported" | "denied" | "off" | "on";

export function webPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** The VAPID public key is url-safe base64; `subscribe` wants the raw bytes. */
function keyBytes(base64Url: string): Uint8Array<ArrayBuffer> {
  const padded = base64Url.replaceAll("-", "+").replaceAll("_", "/");
  const binary = atob(padded.padEnd(Math.ceil(padded.length / 4) * 4, "="));
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.codePointAt(i) ?? 0;
  }
  return bytes;
}

async function currentSubscription(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  return registration ? registration.pushManager.getSubscription() : null;
}

export async function webPushState(): Promise<WebPushState> {
  if (!webPushSupported()) {
    return "unsupported";
  }
  if (Notification.permission === "denied") {
    return "denied";
  }
  return (await currentSubscription()) ? "on" : "off";
}

/** Ask for permission (only now, from a click), subscribe this browser, and register it. */
export async function enableWebPush(publicKey: string): Promise<WebPushState> {
  if (!webPushSupported()) {
    return "unsupported";
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return permission === "denied" ? "denied" : "off";
  }
  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const subscription =
    (await registration.pushManager.getSubscription()) ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyBytes(publicKey),
    }));
  await registerDevice({ platform: "web", token: JSON.stringify(subscription.toJSON()) });
  return "on";
}

/** Unsubscribe this browser and forget it on the server. */
export async function disableWebPush(): Promise<WebPushState> {
  const subscription = await currentSubscription();
  if (subscription) {
    const token = JSON.stringify(subscription.toJSON());
    await subscription.unsubscribe();
    await removeDevice(token);
  }
  return "off";
}
