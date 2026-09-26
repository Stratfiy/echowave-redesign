"use client";

/**
 * One file on a timeline row: the name, the size, and -- for a file an
 * agent drafted (a purchase order, a bid sheet) -- a download.
 *
 * A drafted file lives behind the procurement register, not the knowledge
 * base, so its chip carries `register_id` and `file` and is fetched through
 * the register's own route, which answers only for this workspace. Every
 * other attachment renders exactly as it always did, and so does a drafted
 * one while PROCUREMENT_DOCS_2026_09_ENABLED is off.
 */

import { Download, FileText } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { downloadProcurementFileApiV1ProcurementDocumentsRegisterIdFilesFileGet } from "@/client/sdk.gen";
import { useFeature } from "@/lib/features";

export type AttachedFile = {
    document_uuid: string;
    filename: string;
    size_bytes?: number;
    register_id?: number;
    file?: "docx" | "pdf" | "xlsx";
};

export function sizeOf(bytes?: number): string {
    if (!bytes) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Whether this attachment is a drafted file the register can serve. */
export function isDrafted(file: AttachedFile): boolean {
    return (
        typeof file.register_id === "number" &&
        (file.file === "docx" || file.file === "pdf" || file.file === "xlsx")
    );
}

export function AttachedFileChip({ file, showSize = true }: { file: AttachedFile; showSize?: boolean }) {
    const enabled = useFeature("procurement_docs");
    const [busy, setBusy] = useState(false);
    const size = showSize ? sizeOf(file.size_bytes) : "";
    const chip = "flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm";

    if (!enabled || !isDrafted(file)) {
        return (
            <li className={chip}>
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="max-w-[16rem] truncate">{file.filename}</span>
                {size && <span className="text-xs text-muted-foreground">{size}</span>}
            </li>
        );
    }

    const download = async () => {
        // Opened now, filled in when the link arrives: a tab opened after an
        // await is a popup, and browsers block those.
        const tab = window.open("", "_blank");
        setBusy(true);
        try {
            const response = await downloadProcurementFileApiV1ProcurementDocumentsRegisterIdFilesFileGet({
                path: { register_id: file.register_id as number, file: file.file as "docx" | "pdf" | "xlsx" },
                query: { redirect: false },
            });
            const url = (response.data as { url?: string } | undefined)?.url;
            if (!url) throw new Error("no link");
            if (tab) {
                tab.opener = null;
                tab.location.href = url;
            } else {
                window.location.assign(url);
            }
        } catch {
            tab?.close();
            toast.error(`Could not download ${file.filename}`);
        } finally {
            setBusy(false);
        }
    };

    return (
        <li>
            <button
                type="button"
                onClick={() => void download()}
                disabled={busy}
                className={`${chip} hover:bg-muted disabled:opacity-60`}
                aria-label={`Download ${file.filename}`}
            >
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="max-w-[16rem] truncate">{file.filename}</span>
                {size && <span className="text-xs text-muted-foreground">{size}</span>}
                <Download className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
            </button>
        </li>
    );
}
