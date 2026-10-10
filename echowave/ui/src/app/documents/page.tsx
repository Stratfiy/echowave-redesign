"use client";

/**
 * Documents: the record of money already paid.
 *
 * Nothing is sold here any more (there is no checkout; founder, 9 Oct 2026),
 * so this page replaced Billing and keeps only what the law requires an
 * account to be able to get back: the tax documents issued to it (receipt
 * vouchers, tax invoices and credit notes), the payments they were issued
 * for, and who they are made out to. Nothing is deleted and nothing is hidden;
 * GST rules keep issued documents available for years after the last one.
 */

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  Loader2,
  Mail,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import {
  emailTaxDocumentAgainApiV1BillingDocumentsDocumentIdEmailPost,
  getBillingProfileApiV1BillingProfileGet,
  getTaxDocumentPdfApiV1BillingDocumentsDocumentIdPdfGet,
  listPaymentsApiV1BillingPaymentsGet,
  listTaxDocumentsApiV1BillingDocumentsGet,
  saveBillingProfileApiV1BillingProfilePut,
} from "@/client/sdk.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { DOCUMENTS_TABS } from "@/components/layout/SectionTabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import {
  formatDateTimeIST,
  formatMinor,
  formatPaise,
} from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Payment = {
  id: number;
  order_id: string;
  payment_id: string | null;
  /** What the card was charged, as rupees. */
  gross_paise: number;
  amount_paise: number;
  /** The same figures in the currency the order was placed in. */
  currency?: string;
  amount_minor?: number;
  gross_minor?: number;
  status: string;
  created_at: string | null;
  paid_at: string | null;
};

type BillingProfileFields = {
  legal_name: string | null;
  gstin: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state_code: string | null;
  postal_code: string | null;
  country_code: string;
  billing_email: string | null;
  /** Printed on every document for an enterprise accounts team (KAN-80). */
  po_number?: string | null;
  payment_terms?: string | null;
};

type TaxDocument = {
  id: number;
  kind: string;
  number: string;
  issued_at: string | null;
  period_start: string | null;
  period_end: string | null;
  taxable_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  total_paise: number;
  supply_type: string;
};

const EMPTY_PROFILE: BillingProfileFields = {
  legal_name: "",
  gstin: "",
  address_line1: "",
  address_line2: "",
  city: "",
  state_code: "",
  postal_code: "",
  country_code: "IN",
  billing_email: "",
  po_number: "",
  payment_terms: "",
};

/** The kinds of document an account can hold, in the words on them. */
function documentKindLabel(kind: string): string {
  if (kind === "tax_invoice") return "Tax invoice";
  if (kind === "credit_note") return "Credit note";
  if (kind === "receipt_voucher") return "Receipt";
  // A kind added later is shown as what it is called, not mislabelled as a
  // receipt: an absence of a label cannot be reviewed.
  return kind.replace(/_/g, " ");
}

function StatusBadge({ status }: { status: string }) {
  const shape =
    status === "paid"
      ? {
          icon: CheckCircle2,
          label: "Paid",
          className: "text-emerald-600 dark:text-emerald-400",
        }
      : status === "failed"
        ? {
            icon: XCircle,
            label: "Failed",
            className: "text-red-600 dark:text-red-400",
          }
        : status === "refunded"
          ? {
              icon: RotateCcw,
              label: "Refunded",
              className: "text-muted-foreground",
            }
          : {
              icon: Clock,
              label: "Pending",
              className: "text-muted-foreground",
            };
  const Icon = shape.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-sm",
        shape.className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {shape.label}
    </span>
  );
}

export default function DocumentsPage() {
  const { user, loading: authLoading } = useAuth();
  const hasFetched = useRef(false);

  const [payments, setPayments] = useState<Payment[]>([]);
  const [documents, setDocuments] = useState<TaxDocument[]>([]);
  const [profile, setProfile] = useState<BillingProfileFields>(EMPTY_PROFILE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { isOrganizationAdmin } = useAccessRoles();
  const [savingProfile, setSavingProfile] = useState(false);
  const [emailingDocumentId, setEmailingDocumentId] = useState<number | null>(
    null,
  );
  const [downloadingDocumentId, setDownloadingDocumentId] = useState<
    number | null
  >(null);

  const refresh = useCallback(async () => {
    try {
      const [paymentsResponse, profileResponse, documentsResponse] =
        await Promise.all([
          listPaymentsApiV1BillingPaymentsGet(),
          getBillingProfileApiV1BillingProfileGet(),
          listTaxDocumentsApiV1BillingDocumentsGet(),
        ]);

      if (paymentsResponse.error) {
        setError(detailFromResult(paymentsResponse, "Could not load your payments"));
        return;
      }
      if (profileResponse.error) {
        setError(
          detailFromResult(
            profileResponse,
            "Could not load your details. Please refresh.",
          ),
        );
        return;
      }
      if (documentsResponse.error) {
        setError(
          detailFromResult(
            documentsResponse,
            "Could not load your documents. Please refresh.",
          ),
        );
        return;
      }

      setPayments(
        (paymentsResponse.data as unknown as { payments: Payment[] })
          .payments ?? [],
      );
      const loaded = profileResponse.data as unknown as {
        profile: BillingProfileFields;
      };
      // Nulls become empty strings: a controlled input handed null flips
      // to uncontrolled and React drops the value on the next render.
      setProfile({
        ...EMPTY_PROFILE,
        ...Object.fromEntries(
          Object.entries(loaded.profile).map(([k, v]) => [k, v ?? ""]),
        ),
        country_code: loaded.profile.country_code || "IN",
      });
      setDocuments(
        (documentsResponse.data as unknown as { documents: TaxDocument[] })
          .documents ?? [],
      );
      setError(null);
    } catch {
      setError(
        "Could not load your documents. Check your connection and refresh to try again.",
      );
    }
  }, []);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    void (async () => {
      await refresh();
      setLoading(false);
    })();
  }, [authLoading, user, refresh]);

  const saveProfile = useCallback(async () => {
    setError(null);
    setNotice(null);
    setSavingProfile(true);
    try {
      const response = await saveBillingProfileApiV1BillingProfilePut({
        body: {
          legal_name: profile.legal_name || null,
          gstin: profile.gstin || null,
          address_line1: profile.address_line1 || null,
          address_line2: profile.address_line2 || null,
          city: profile.city || null,
          state_code: profile.state_code || null,
          postal_code: profile.postal_code || null,
          country_code: profile.country_code || "IN",
          billing_email: profile.billing_email || null,
          po_number: profile.po_number || null,
          payment_terms: profile.payment_terms || null,
        },
      });
      if (response.error) {
        setError(detailFromResult(response, "Could not save your details"));
        return;
      }
      setNotice("Details saved.");
      await refresh();
    } catch {
      setError("Could not save your details. Please try again.");
    } finally {
      setSavingProfile(false);
    }
  }, [profile, refresh]);

  // Sending a document again is a delivery, not a reissue: same number, same
  // snapshot, same PDF. It exists because the send at issue time happens once
  // and has one unrecoverable failure -- a document issued before the account
  // had a billing email is never sent, and completing the profile afterwards
  // does not go back for it.
  const emailDocument = useCallback(async (doc: TaxDocument) => {
    setError(null);
    setNotice(null);
    setEmailingDocumentId(doc.id);
    try {
      const response =
        await emailTaxDocumentAgainApiV1BillingDocumentsDocumentIdEmailPost({
          path: { document_id: doc.id },
        });
      if (response.error) {
        setError(detailFromResult(response, "Could not send the document"));
        return;
      }
      const sentTo = (response.data as { to?: string } | undefined)?.to;
      setNotice(
        sentTo ? `${doc.number} sent to ${sentTo}.` : `${doc.number} sent.`,
      );
    } catch {
      setError("Could not send the document. Please try again.");
    } finally {
      setEmailingDocumentId(null);
    }
  }, []);

  const downloadDocument = useCallback(async (doc: TaxDocument) => {
    setError(null);
    setDownloadingDocumentId(doc.id);
    try {
      const response =
        await getTaxDocumentPdfApiV1BillingDocumentsDocumentIdPdfGet({
          path: { document_id: doc.id },
          parseAs: "blob",
        });
      if (response.error || !response.data) {
        setError(detailFromResult(response, "Could not download the PDF"));
        return;
      }
      const blob = response.data as Blob;
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${doc.number.replace(/\//g, "-")}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setError("Could not download the PDF. Please try again.");
    } finally {
      setDownloadingDocumentId(null);
    }
  }, []);

  const setProfileField = (field: keyof BillingProfileFields, value: string) =>
    setProfile((current) => ({ ...current, [field]: value }));

  // A GSTIN's first two digits *are* the state code, and the server rejects a
  // profile where the two disagree -- correctly, since one of them would be
  // wrong and the choice decides CGST+SGST versus IGST. So the GSTIN fills the
  // state in, the way the server already does when the state is left blank.
  // The field stays editable for anyone who genuinely needs to differ.
  const setGstin = (value: string) =>
    setProfile((current) => {
      const derived =
        value.length >= 2 && /^\d{2}$/.test(value.slice(0, 2))
          ? value.slice(0, 2)
          : null;
      return {
        ...current,
        gstin: value,
        state_code: derived ?? current.state_code,
      };
    });

  /**
   * The page's title band, on every branch: a skeleton, an unavailable state,
   * and the real thing. Only the last used to carry a heading, so a slow or
   * failing load dropped the reader onto an untitled page.
   */
  const shell = (body: ReactNode) => (
    <>
      <PageHeader
        tabs={DOCUMENTS_TABS}
        title="Documents"
        description="Your tax documents, receipts, credit notes and payment history."
      />
      <PageBody>{body}</PageBody>
    </>
  );

  if (loading) {
    return shell(
      <div className="space-y-6">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>,
    );
  }

  return shell(
    <div className="space-y-8">
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => void refresh()}
          >
            Retry
          </Button>
        </div>
      )}

      {notice && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-700 dark:border-indigo-900/50 dark:bg-indigo-950/30 dark:text-indigo-300"
        >
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      <section className="rounded-xl border bg-card p-6">
        <h2 className="text-lg font-medium">Tax documents</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Every receipt, tax invoice and credit note issued to your account.
          They stay here, and can be downloaded or sent again, for as long as
          the law asks them to be kept.
        </p>
        {documents.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">
            Nothing has been issued to your account.
          </p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Number</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead className="text-right">Taxable</TableHead>
                  <TableHead className="text-right">GST</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((doc) => {
                  const gst = doc.cgst_paise + doc.sgst_paise + doc.igst_paise;
                  return (
                    <TableRow key={doc.id}>
                      <TableCell className="whitespace-nowrap font-mono text-xs">
                        {doc.number}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm">
                        {documentKindLabel(doc.kind)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {formatDateTimeIST(doc.issued_at)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPaise(doc.taxable_paise)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {doc.supply_type === "export"
                          ? "Zero-rated"
                          : formatPaise(gst)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPaise(doc.total_paise)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          disabled={emailingDocumentId === doc.id}
                          onClick={() => void emailDocument(doc)}
                          aria-label={`Email ${doc.number}`}
                          title="Send to your email for documents"
                        >
                          {emailingDocumentId === doc.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Mail className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          disabled={downloadingDocumentId === doc.id}
                          onClick={() => void downloadDocument(doc)}
                          aria-label={`Download ${doc.number}`}
                        >
                          {downloadingDocumentId === doc.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Download className="h-4 w-4" />
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      <section className="rounded-xl border bg-card p-6">
        <h2 className="text-lg font-medium">Payment history</h2>
        {payments.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">
            No payments on record.
          </p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Reference</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payments.map((payment) => (
                  <TableRow key={payment.id}>
                    <TableCell className="whitespace-nowrap">
                      {formatDateTimeIST(payment.paid_at ?? payment.created_at)}
                    </TableCell>
                    {/* What the card statement shows: the gross, with tax. */}
                    <TableCell className="text-right tabular-nums">
                      {formatMinor(
                        payment.gross_minor ??
                          payment.gross_paise ??
                          payment.amount_paise,
                        payment.currency,
                      )}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={payment.status} />
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {/* The payment id is what support and the bank both
                          recognise; the order id only exists until it is
                          paid. */}
                      {payment.payment_id ?? payment.order_id}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      <section className="rounded-xl border bg-card p-6">
        <h2 className="text-lg font-medium">Details on your documents</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Who documents are made out to. Your state decides whether GST is shown
          as CGST + SGST or as IGST, so it has to match your GSTIN.
        </p>

        {/* Editing this is admin-gated on the server (PUT
            /api/v1/billing/profile), because it decides what tax a document
            carries. A member can read it -- it is their own company's
            details -- but the fieldset is disabled rather than the section
            hidden, so they can see what is on file and who to ask. */}
        {!isOrganizationAdmin && (
          <p className="mt-3 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            Only an organization Admin or Owner can change these. Ask one of
            them if something here is wrong.
          </p>
        )}

        <fieldset disabled={!isOrganizationAdmin} className="contents">
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label htmlFor="legal-name">Registered business name</Label>
              <Input
                id="legal-name"
                value={profile.legal_name ?? ""}
                onChange={(e) => setProfileField("legal_name", e.target.value)}
                placeholder="Acme Technologies Private Limited"
                className="mt-1.5"
              />
            </div>

            <div>
              <Label htmlFor="gstin">GSTIN</Label>
              <Input
                id="gstin"
                value={profile.gstin ?? ""}
                onChange={(e) => setGstin(e.target.value.toUpperCase())}
                placeholder="29ABCDE1234F1Z5"
                maxLength={15}
                className="mt-1.5 font-mono"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Leave blank if you are not registered.
              </p>
            </div>

            <div>
              <Label htmlFor="billing-email">Email for documents</Label>
              <Input
                id="billing-email"
                type="email"
                value={profile.billing_email ?? ""}
                onChange={(e) =>
                  setProfileField("billing_email", e.target.value)
                }
                placeholder="accounts@acme.com"
                className="mt-1.5"
              />
            </div>

            <div>
              <Label htmlFor="po-number">PO number (optional)</Label>
              <Input
                id="po-number"
                value={profile.po_number ?? ""}
                onChange={(e) => setProfileField("po_number", e.target.value)}
                placeholder="Your purchase order, printed on documents"
                className="mt-1.5"
              />
            </div>

            <div>
              <Label htmlFor="payment-terms">Payment terms (optional)</Label>
              <Input
                id="payment-terms"
                value={profile.payment_terms ?? ""}
                onChange={(e) =>
                  setProfileField("payment_terms", e.target.value)
                }
                placeholder="e.g. Net 45"
                className="mt-1.5"
              />
            </div>

            <div className="sm:col-span-2">
              <Label htmlFor="address1">Address</Label>
              <Input
                id="address1"
                value={profile.address_line1 ?? ""}
                onChange={(e) =>
                  setProfileField("address_line1", e.target.value)
                }
                placeholder="Street address"
                className="mt-1.5"
              />
              <Input
                aria-label="Address line 2"
                value={profile.address_line2 ?? ""}
                onChange={(e) =>
                  setProfileField("address_line2", e.target.value)
                }
                placeholder="Building, floor (optional)"
                className="mt-2"
              />
            </div>

            <div>
              <Label htmlFor="city">City</Label>
              <Input
                id="city"
                value={profile.city ?? ""}
                onChange={(e) => setProfileField("city", e.target.value)}
                className="mt-1.5"
              />
            </div>

            <div>
              <Label htmlFor="postal">PIN / postal code</Label>
              <Input
                id="postal"
                value={profile.postal_code ?? ""}
                onChange={(e) => setProfileField("postal_code", e.target.value)}
                className="mt-1.5"
              />
            </div>

            <div>
              <Label htmlFor="state-code">GST state code</Label>
              <Input
                id="state-code"
                value={profile.state_code ?? ""}
                onChange={(e) => setProfileField("state_code", e.target.value)}
                placeholder="29"
                maxLength={2}
                className="mt-1.5 font-mono"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                The first two digits of your GSTIN.
              </p>
            </div>

            <div>
              <Label htmlFor="country">Country</Label>
              <Input
                id="country"
                value={profile.country_code ?? "IN"}
                onChange={(e) =>
                  setProfileField("country_code", e.target.value.toUpperCase())
                }
                placeholder="IN"
                maxLength={2}
                className="mt-1.5 font-mono"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Outside India is a zero-rated export.
              </p>
            </div>
          </div>

          <Button
            type="button"
            onClick={() => void saveProfile()}
            disabled={savingProfile}
            className="mt-5"
          >
            {savingProfile ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving…
              </>
            ) : (
              "Save details"
            )}
          </Button>
        </fieldset>
      </section>
    </div>,
  );
}
