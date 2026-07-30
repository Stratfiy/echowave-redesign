"use client";

/**
 * Customer-facing telephony verification.
 *
 * Two things this screen has to get right. First, it must never suggest that
 * uploading documents unlocks calling — the licensed operator decides that, and
 * an expectation set here is a support ticket later. Second, it must be obvious
 * that browser testing needs none of this, so nobody sits waiting on paperwork
 * before trying their agent.
 */

import {
    AlertTriangle,
    CheckCircle2,
    Clock,
    FileUp,
    Loader2,
    PhoneOff,
    ShieldCheck,
    Trash2,
    Upload,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    deleteDocumentApiV1KycDocumentsDocumentIdDelete,
    getKycApiV1KycGet,
    setBusinessDetailsApiV1KycBusinessDetailsPut,
    submitKycApiV1KycSubmitPost,
    uploadDocumentApiV1KycDocumentsPost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type KycDocument = {
    id: number;
    kind: string;
    filename: string | null;
    size_bytes: number | null;
    uploaded_at: string | null;
};

type KycView = {
    status: string;
    business_type: string | null;
    legal_name: string | null;
    gstin: string | null;
    submitted_at: string | null;
    rejection_reason: string | null;
    carrier_rejection_reason: string | null;
    required_documents: string[];
    missing_documents: string[];
    documents: KycDocument[];
    can_submit: boolean;
    telephony_enabled: boolean;
};

const DOCUMENT_LABELS: Record<string, string> = {
    certificate_of_incorporation: "Certificate of incorporation",
    gst_certificate: "GST certificate",
    authorised_signatory_id: "Authorised signatory ID",
    address_proof: "Address proof",
};

const DOCUMENT_HINTS: Record<string, string> = {
    certificate_of_incorporation: "As issued by the MCA.",
    gst_certificate: "The registration certificate, not a return.",
    authorised_signatory_id: "Aadhaar, PAN or passport of the person signing.",
    address_proof: "A utility bill or bank statement from the last three months.",
};

/**
 * What the customer is told at each stage.
 *
 * Written as "where this is" rather than "what you did", because for four of
 * these six states the answer to "what now?" is *wait*, and a status line that
 * does not say so reads as a stalled form.
 */
const STAGE: Record<
    string,
    { title: string; body: string; tone: "neutral" | "good" | "warn" | "bad"; icon: React.ReactNode }
> = {
    not_started: {
        title: "Not started",
        body: "Phone numbers need verification by a licensed operator. Browser calls work without it.",
        tone: "neutral",
        icon: <FileUp className="h-4 w-4" />,
    },
    submitted: {
        title: "With our team",
        body: "We are checking your documents are complete and legible before sending them on. Usually within a working day.",
        tone: "neutral",
        icon: <Clock className="h-4 w-4" />,
    },
    under_review: {
        title: "Being reviewed",
        body: "Someone on our team is looking at your documents now.",
        tone: "neutral",
        icon: <Clock className="h-4 w-4" />,
    },
    rejected: {
        title: "Needs a change",
        body: "Fix what is noted below and submit again.",
        tone: "warn",
        icon: <AlertTriangle className="h-4 w-4" />,
    },
    forwarded: {
        title: "With the telecom operator",
        body: "Your documents are with the licensed operator, who has to approve them before calls can be placed. This usually takes a few working days.",
        tone: "neutral",
        icon: <Clock className="h-4 w-4" />,
    },
    carrier_approved: {
        title: "Verified",
        body: "You can place and receive calls on phone numbers.",
        tone: "good",
        icon: <ShieldCheck className="h-4 w-4" />,
    },
    carrier_rejected: {
        title: "The operator did not approve this",
        body: "See their reason below, then correct your documents and submit again.",
        tone: "bad",
        icon: <PhoneOff className="h-4 w-4" />,
    },
};

const EDITABLE = new Set(["not_started", "rejected", "carrier_rejected"]);

export default function VerificationPage() {
    const { user, loading: authLoading } = useAuth();
    const authReady = !authLoading && Boolean(user);

    const [view, setView] = useState<KycView | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [uploading, setUploading] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);

    const [businessType, setBusinessType] = useState("");
    const [legalName, setLegalName] = useState("");
    const [gstin, setGstin] = useState("");

    const load = useCallback(async () => {
        const result = await getKycApiV1KycGet();
        if (result.error) {
            setError(detailFromError(result.error, "Failed to load your verification"));
        } else {
            const next = result.data as unknown as KycView;
            setView(next);
            setBusinessType(next.business_type ?? "");
            setLegalName(next.legal_name ?? "");
            setGstin(next.gstin ?? "");
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    const saveDetails = async () => {
        setSaving(true);
        setError(null);
        const result = await setBusinessDetailsApiV1KycBusinessDetailsPut({
            body: {
                business_type: businessType,
                legal_name: legalName,
                gstin: gstin.trim() || null,
            },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Could not save your business details"));
        } else {
            setView(result.data as unknown as KycView);
        }
        setSaving(false);
    };

    const upload = async (kind: string, file: File) => {
        setUploading(kind);
        setError(null);
        const body = new FormData();
        body.append("kind", kind);
        body.append("file", file);
        const result = await uploadDocumentApiV1KycDocumentsPost({
            body: body as never,
        });
        if (result.error) {
            setError(detailFromError(result.error, "Could not upload that file"));
        } else {
            setView(result.data as unknown as KycView);
        }
        setUploading(null);
    };

    const removeDocument = async (document_id: number) => {
        setError(null);
        const result = await deleteDocumentApiV1KycDocumentsDocumentIdDelete({
            path: { document_id },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Could not remove that document"));
        } else {
            setView(result.data as unknown as KycView);
        }
    };

    const submit = async () => {
        setSubmitting(true);
        setError(null);
        const result = await submitKycApiV1KycSubmitPost();
        if (result.error) {
            setError(detailFromError(result.error, "Could not submit for review"));
        } else {
            setView(result.data as unknown as KycView);
        }
        setSubmitting(false);
    };

    const editable = view ? EDITABLE.has(view.status) : false;
    const stage = view ? STAGE[view.status] : null;
    const documentsByKind = new Map(
        (view?.documents ?? []).map((document) => [document.kind, document]),
    );

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-3xl px-6 pb-12 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Telephony verification
                    </h1>
                    <p className="mt-1.5 text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        Indian regulation requires the licensed telecom operator to verify
                        every business using a phone number. Testing your agent in the
                        browser needs none of this.
                    </p>
                </header>

                {loading ? (
                    <Skeleton className="h-[420px] w-full rounded-2xl" />
                ) : (
                    <>
                        {stage && (
                            <section
                                className={cn(
                                    "glass-panel mb-5 flex items-start gap-3 px-5 py-4",
                                    stage.tone === "good" &&
                                        "ring-1 ring-emerald-500/30",
                                    stage.tone === "warn" &&
                                        "ring-1 ring-[color:var(--glass-amber-edge)]",
                                    stage.tone === "bad" && "ring-1 ring-destructive/35",
                                )}
                            >
                                <span className="mt-0.5 text-muted-foreground">
                                    {stage.icon}
                                </span>
                                <div>
                                    <p className="text-sm font-medium tracking-[-0.014em] text-foreground">
                                        {stage.title}
                                    </p>
                                    <p className="mt-0.5 text-sm text-muted-foreground">
                                        {stage.body}
                                    </p>
                                    {view?.rejection_reason && (
                                        <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                                            {view.rejection_reason}
                                        </p>
                                    )}
                                    {view?.carrier_rejection_reason && (
                                        <p className="mt-2 text-sm text-destructive">
                                            {view.carrier_rejection_reason}
                                        </p>
                                    )}
                                </div>
                            </section>
                        )}

                        {error && (
                            <section className="glass-panel mb-5 flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                                <AlertTriangle className="h-4 w-4 shrink-0" />
                                {error}
                            </section>
                        )}

                        <section className="glass-panel px-6 pb-6 pt-5">
                            <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                                Your business
                            </h2>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                                This decides which documents the operator needs.
                            </p>

                            <div className="mt-4 grid gap-4 sm:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label htmlFor="business-type">Registered as</Label>
                                    <Select
                                        value={businessType}
                                        onValueChange={setBusinessType}
                                        disabled={!editable}
                                    >
                                        <SelectTrigger id="business-type">
                                            <SelectValue placeholder="Choose one" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="company">
                                                Company / LLP
                                            </SelectItem>
                                            <SelectItem value="individual">
                                                Individual / proprietorship
                                            </SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <Label htmlFor="legal-name">Legal name</Label>
                                    <Input
                                        id="legal-name"
                                        value={legalName}
                                        disabled={!editable}
                                        onChange={(event) => setLegalName(event.target.value)}
                                        placeholder="Exactly as on the certificate"
                                    />
                                </div>
                                <div className="space-y-1.5 sm:col-span-2">
                                    <Label htmlFor="gstin">GSTIN (optional)</Label>
                                    <Input
                                        id="gstin"
                                        value={gstin}
                                        disabled={!editable}
                                        onChange={(event) => setGstin(event.target.value)}
                                        placeholder="29ABCDE1234F1Z5"
                                        className="font-mono"
                                    />
                                </div>
                            </div>

                            {editable && (
                                <Button
                                    className="mt-4"
                                    variant="outline"
                                    disabled={saving || !businessType || !legalName.trim()}
                                    onClick={saveDetails}
                                >
                                    {saving && (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    )}
                                    Save details
                                </Button>
                            )}
                        </section>

                        {(view?.required_documents.length ?? 0) > 0 && (
                            <section className="glass-panel mt-5 px-6 pb-6 pt-5">
                                <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                                    Documents
                                </h2>
                                <p className="mt-0.5 text-xs text-muted-foreground">
                                    PDF, JPEG or PNG, up to 10&nbsp;MB each.
                                </p>

                                <ul className="mt-4 space-y-2">
                                    {view!.required_documents.map((kind) => (
                                        <DocumentRow
                                            key={kind}
                                            kind={kind}
                                            document={documentsByKind.get(kind)}
                                            editable={editable}
                                            uploading={uploading === kind}
                                            onUpload={(file) => upload(kind, file)}
                                            onRemove={removeDocument}
                                        />
                                    ))}
                                </ul>

                                {editable && (
                                    <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-foreground/[0.07] pt-5">
                                        <Button
                                            disabled={!view!.can_submit || submitting}
                                            onClick={submit}
                                        >
                                            {submitting && (
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            )}
                                            Submit for verification
                                        </Button>
                                        {!view!.can_submit && (
                                            <span className="text-xs text-muted-foreground">
                                                {view!.missing_documents.length > 0
                                                    ? "Upload everything above first."
                                                    : "Fill in your business details first."}
                                            </span>
                                        )}
                                    </div>
                                )}
                            </section>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

function DocumentRow({
    kind,
    document: uploaded,
    editable,
    uploading,
    onUpload,
    onRemove,
}: {
    kind: string;
    document?: KycDocument;
    editable: boolean;
    uploading: boolean;
    onUpload: (file: File) => void;
    onRemove: (documentId: number) => void;
}) {
    const inputRef = useRef<HTMLInputElement>(null);

    return (
        <li className="glass-tile flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-medium tracking-[-0.012em] text-foreground">
                    {uploaded && (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                    )}
                    {DOCUMENT_LABELS[kind] ?? kind}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                    {uploaded?.filename ?? DOCUMENT_HINTS[kind] ?? ""}
                </p>
            </div>

            <div className="flex items-center gap-2">
                {editable && (
                    <>
                        {/* Hidden input with a real button in front of it — the
                            native file control styles inconsistently. */}
                        <input
                            ref={inputRef}
                            type="file"
                            accept="application/pdf,image/jpeg,image/png"
                            className="hidden"
                            onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) onUpload(file);
                                event.target.value = "";
                            }}
                        />
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={uploading}
                            onClick={() => inputRef.current?.click()}
                        >
                            {uploading ? (
                                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <Upload className="mr-2 h-3.5 w-3.5" />
                            )}
                            {uploaded ? "Replace" : "Upload"}
                        </Button>
                        {uploaded && (
                            <Button
                                variant="ghost"
                                size="sm"
                                aria-label={`Remove ${DOCUMENT_LABELS[kind] ?? kind}`}
                                onClick={() => onRemove(uploaded.id)}
                            >
                                <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                        )}
                    </>
                )}
            </div>
        </li>
    );
}
