/**
 * "Money just moved" — a window event the balance chip listens for.
 *
 * The chip polls once a minute, which is fine for a number that drifts and
 * wrong for the moment a call ends or credits land: the person is looking
 * at the chip right then, and a minute is long enough to conclude the call
 * was free. Anything that knows the balance changed says so here.
 */

export const BALANCE_CHANGED_EVENT = "decibyl:balance-changed";

export function announceBalanceChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(BALANCE_CHANGED_EVENT));
}
