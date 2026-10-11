/**
 * Whether a price is shown to the people using Decibyl.
 *
 * The founder decided on 9 Oct 2026: no pricing is shown to users -- no
 * price, plan name, credit rate or upgrade prompt (AGENTS.md, "Product
 * decisions that are already settled"). Every place that used to show a rate
 * outside the checkout reads this, so the figures stay in the code, wired and
 * tested, and come back with one line once new price copy is approved.
 *
 * Staff and admin cost screens (superadmin) do not read it: they are not user
 * facing. The checkout itself -- plans, top-ups, autopay -- stays behind
 * `free_mode`, because nothing is charged while that is on, and the moment
 * something is charged the price has to be shown before payment.
 *
 * `pricingGuard.test.ts` fails on a user-facing string with a price in it
 * unless it sits behind this switch or in an allowlisted file.
 */
export const PRICES_SHOWN: boolean = false;
