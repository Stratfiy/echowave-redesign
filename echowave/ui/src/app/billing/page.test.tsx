import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ balance: vi.fn(), payments: vi.fn(), profile: vi.fn(), documents: vi.fn(), topup: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
  getBalanceApiV1BillingBalanceGet: api.balance,
  listPaymentsApiV1BillingPaymentsGet: api.payments,
  getBillingProfileApiV1BillingProfileGet: api.profile,
  listTaxDocumentsApiV1BillingDocumentsGet: api.documents,
  createTopupApiV1BillingTopupPost: api.topup,
  saveBillingProfileApiV1BillingProfilePut: vi.fn(),
  emailTaxDocumentAgainApiV1BillingDocumentsDocumentIdEmailPost: vi.fn(),
  getTaxDocumentPdfApiV1BillingDocumentsDocumentIdPdfGet: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => ({ isOrganizationAdmin: true }) }));
vi.mock("@/components/billing/PlanSection", () => ({ PlanSection: () => null }));
vi.mock("@/lib/billing/checkout", () => ({ loadCheckout: () => Promise.resolve() }));
import BillingPage from "./page";

const balance = { balance_paise: 200000, topups_enabled: true, min_topup_paise: 10000, max_topup_paise: 10000000, topup_increment_paise: 10000, min_balance_paise: 5000, calling_blocked: false, gst_rate_basis_points: 1800, is_export: false, billing_profile_complete: true };
let checkout: { handler: () => void; amount: number; order_id: string };
beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.balance.mockResolvedValue({ data: balance });
  api.payments.mockResolvedValue({ data: { payments: [] } });
  api.profile.mockResolvedValue({ data: { profile: { country_code: "IN" }, is_complete: true } });
  api.documents.mockResolvedValue({ data: { documents: [] } });
  api.topup.mockResolvedValue({ data: { order_id: "order_current", gross_paise: 236000, currency: "INR", key_id: "test" } });
  window.Razorpay = class { constructor(options: typeof checkout) { checkout = options; } open() {} };
});
afterEach(() => { vi.useRealTimers(); delete window.Razorpay; });
async function mount() { await act(async () => { render(<BillingPage />); }); }
async function pay() {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Pay with Razorpay/i })); });
  act(() => checkout.handler());
}
async function poll() { await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); }
describe("billing confirmation", () => {
  it("confirms the specific paid order even when call spending lowered the wallet balance", async () => {
    await mount(); await pay();
    expect(checkout.amount).toBe(236000);
    expect(checkout.order_id).toBe("order_current");
    api.balance.mockResolvedValue({ data: { ...balance, balance_paise: 190000 } });
    api.payments.mockResolvedValue({ data: { payments: [{ id: 1, order_id: "order_current", status: "paid", amount_paise: 200000 }] } });
    await poll();
    expect(screen.getByRole("status").textContent).toContain("Payment received");
    expect(api.profile).toHaveBeenCalledTimes(1);
  });
  it("does not confirm an unrelated top-up even when the balance increased", async () => {
    await mount(); await pay();
    api.balance.mockResolvedValue({ data: { ...balance, balance_paise: 400000 } });
    api.payments.mockResolvedValue({ data: { payments: [{ id: 2, order_id: "order_other", status: "paid", amount_paise: 200000 }] } });
    await poll();
    expect(screen.getByRole("status").textContent).toContain("Confirming");
    expect(screen.queryByText(/Payment received/)).toBeNull();
  });
  it("reports failure of the current order", async () => {
    await mount(); await pay();
    api.payments.mockResolvedValue({ data: { payments: [{ id: 1, order_id: "order_current", status: "failed", amount_paise: 200000 }] } });
    await poll();
    expect(screen.getByRole("alert").textContent).toContain("not completed");
  });
  it("recovers from an initial network failure without displaying a fake zero balance", async () => {
    api.balance.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await mount();
    expect(screen.getByRole("alert").textContent).toContain("Could not load billing");
    expect(screen.queryByText("Available credit")).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry loading billing" })); });
    expect(screen.getByText("Available credit")).toBeTruthy();
  });
  it("reports missing documents instead of calling them an empty history", async () => {
    api.documents.mockResolvedValueOnce({ error: { detail: "Document service unavailable" } });
    await mount();
    expect(screen.getByRole("alert").textContent).toContain("Document service unavailable");
  });
  it("bounds retries when confirmation is delayed", async () => {
    await mount(); await pay();
    await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(screen.getByRole("status").textContent).toContain("still being confirmed");
    expect(api.payments).toHaveBeenCalledTimes(11);
  });
});
