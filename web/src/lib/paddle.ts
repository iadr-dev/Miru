/** Paddle.js wrapper for the billing page's checkout overlay.
 *
 * Paddle Billing has no hosted checkout page: a checkout is opened by Paddle.js running on a page
 * we own. The server creates the transaction (tagged with the user id so the webhook can attribute
 * the subscription), and this opens the overlay for it. The client-side token is publishable by
 * design (it can only open checkouts and preview prices); the API key never reaches the browser.
 *
 * Entitlement is granted by Paddle's webhook, never by this browser event - the completed event
 * only tells the page to start looking for the subscription. */
import { initializePaddle, type Paddle } from "@paddle/paddle-js";

export type PaddleEnvironment = "sandbox" | "production";

type CheckoutEvent = { name?: string };
type CheckoutListener = (event: CheckoutEvent) => void;

let instance: Promise<Paddle | undefined> | null = null;
let listener: CheckoutListener | null = null;

/** Who hears Paddle's checkout events (completed, closed, ...). One listener at a time: the
 * billing panel registers on mount and clears on unmount. */
export function setCheckoutListener(next: CheckoutListener | null): void {
  listener = next;
}

/** Load and initialise Paddle.js once per page. Resolves undefined if the script can't load. */
export function loadPaddle(
  token: string,
  environment: PaddleEnvironment,
): Promise<Paddle | undefined> {
  instance ??= initializePaddle({
    token,
    environment,
    eventCallback: (event) => listener?.(event.name ? { name: event.name } : {}),
  });
  return instance;
}

/** Paddle's checkout locale for a Miru UI locale (Paddle uses `zh-Hant` for Traditional). */
export function paddleLocale(locale: string): string {
  return locale === "zh-TW" ? "zh-Hant" : "en";
}

export const CHECKOUT_COMPLETED = "checkout.completed";
