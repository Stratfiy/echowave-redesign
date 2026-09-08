import { afterEach, describe, expect, it, vi } from "vitest";

import { loadCheckout } from "./checkout";
const script = () => document.querySelector<HTMLScriptElement>('script[src*="checkout.razorpay.com"]')!;
afterEach(() => { delete window.Razorpay; script()?.remove(); vi.useRealTimers(); });
describe("checkout script", () => {
  it("shares concurrent requests and retries after a network failure", async () => {
    const first = loadCheckout();
    expect(loadCheckout()).toBe(first);
    const rejected = expect(first).rejects.toThrow("Could not load");
    script().dispatchEvent(new Event("error"));
    await rejected;
    expect(script()).toBeNull();
    const retry = loadCheckout();
    window.Razorpay = class { open() {} };
    script().dispatchEvent(new Event("load"));
    await expect(retry).resolves.toBeUndefined();
  });
  it("rejects a loaded script without the SDK", async () => {
    const result = expect(loadCheckout()).rejects.toThrow("did not load");
    script().dispatchEvent(new Event("load"));
    await result;
  });
  it("times out a hanging script and allows another attempt", async () => {
    vi.useFakeTimers();
    const result = expect(loadCheckout()).rejects.toThrow("too long");
    await vi.advanceTimersByTimeAsync(15000);
    await result;
    expect(script()).toBeNull();
    const retry = expect(loadCheckout()).rejects.toThrow("Could not load");
    script().dispatchEvent(new Event("error"));
    await retry;
  });
});
