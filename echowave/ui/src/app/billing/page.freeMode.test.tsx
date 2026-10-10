import { act, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// No pricing is shown to users (founder decision, 9 Oct 2026; lib/pricing.ts).
// While nothing is charged (free_mode) the Billing page keeps only the record
// of payments already made: no balance in credits, no rate card, no plans and
// no top-up.
const api = vi.hoisted(() => ({ balance: vi.fn(), payments: vi.fn(), profile: vi.fn(), documents: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
  getBalanceApiV1BillingBalanceGet: api.balance,
  getReferralsApiV1ReferralsGet: vi.fn().mockResolvedValue({ data: null }),
  listPaymentsApiV1BillingPaymentsGet: api.payments,
  getBillingProfileApiV1BillingProfileGet: api.profile,
  listTaxDocumentsApiV1BillingDocumentsGet: api.documents,
  createTopupApiV1BillingTopupPost: vi.fn(),
  saveBillingProfileApiV1BillingProfilePut: vi.fn(),
  emailTaxDocumentAgainApiV1BillingDocumentsDocumentIdEmailPost: vi.fn(),
  getTaxDocumentPdfApiV1BillingDocumentsDocumentIdPdfGet: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => ({ isOrganizationAdmin: true }) }));
vi.mock("@/components/billing/PlanSection", () => ({ PlanSection: () => <p>plan picker</p> }));
vi.mock("@/components/billing/RateCardSection", () => ({
  RateCardSection: () => <p>rate card</p>,
  RunwayLine: () => null,
}));
vi.mock("@/lib/billing/checkout", () => ({ loadCheckout: () => Promise.resolve() }));
vi.mock("@/lib/features", () => ({ useFeature: (flag: string) => flag === "free_mode" }));
import BillingPage from "./page";

const balance = { balance_paise: 200000, topups_enabled: true, min_topup_paise: 10000, max_topup_paise: 10000000, topup_increment_paise: 10000, min_balance_paise: 5000, calling_blocked: false, gst_rate_basis_points: 1800, is_export: false, billing_profile_complete: true };

beforeEach(() => {
  vi.clearAllMocks();
  api.balance.mockResolvedValue({ data: balance });
  api.payments.mockResolvedValue({ data: { payments: [] } });
  api.profile.mockResolvedValue({ data: { profile: { country_code: "IN" }, is_complete: true } });
  api.documents.mockResolvedValue({ data: { documents: [] } });
});

describe("billing while nothing is charged", () => {
  it("shows no balance, rate card, plans or top-up, and keeps the records", async () => {
    await act(async () => {
      render(<BillingPage />);
    });
    expect(screen.queryByText("Available credits")).toBeNull();
    expect(screen.queryByText("rate card")).toBeNull();
    expect(screen.queryByText("plan picker")).toBeNull();
    expect(screen.queryByText("Add credit")).toBeNull();
    expect(screen.queryByText(/prepaid/)).toBeNull();
    expect(screen.getByText("Tax documents")).toBeTruthy();
    expect(screen.getByText("Payment history")).toBeTruthy();
  });
});
