/**
 * A file an agent drafted (a purchase order, a bid sheet) is a download on
 * the thread and on /deliverables; every other attachment, and a drafted one
 * while the switch is off, is the plain chip it always was.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ procurement_docs: false }));
const download = vi.hoisted(() => vi.fn());

vi.mock("@/lib/features", () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));
vi.mock("@/client/sdk.gen", () => ({
    downloadProcurementFileApiV1ProcurementDocumentsRegisterIdFilesFileGet: download,
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

import { AttachedFileChip } from "../AttachedFileChip";

const drafted = {
    document_uuid: "procurement-12-pdf",
    filename: "PO-26-27-0001.pdf",
    size_bytes: 20480,
    register_id: 12,
    file: "pdf" as const,
};

function renderChip(file: Parameters<typeof AttachedFileChip>[0]["file"]) {
    return render(
        <ul>
            <AttachedFileChip file={file} />
        </ul>,
    );
}

beforeEach(() => {
    download.mockReset();
    flags.procurement_docs = false;
});
afterEach(() => vi.restoreAllMocks());

describe("a file on a timeline row", () => {
    it("is a plain chip while the switch is off", () => {
        renderChip(drafted);
        expect(screen.getByText("PO-26-27-0001.pdf")).toBeTruthy();
        expect(screen.getByText("20 KB")).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
    });

    it("is a plain chip for an ordinary attachment even when on", () => {
        flags.procurement_docs = true;
        renderChip({ document_uuid: "d1", filename: "quote.pdf" });
        expect(screen.queryByRole("button")).toBeNull();
    });

    it("downloads a drafted file through the register when on", async () => {
        flags.procurement_docs = true;
        const tab = { opener: {}, location: { href: "" }, close: vi.fn() };
        vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
        download.mockResolvedValue({ data: { url: "https://files.test/po.pdf", filename: "PO-26-27-0001.pdf" } });
        renderChip(drafted);
        fireEvent.click(screen.getByRole("button", { name: "Download PO-26-27-0001.pdf" }));
        await waitFor(() => expect(tab.location.href).toBe("https://files.test/po.pdf"));
        expect(download.mock.calls[0][0]).toEqual({
            path: { register_id: 12, file: "pdf" },
            query: { redirect: false },
        });
        expect(tab.opener).toBeNull();
    });

    it("closes the tab it opened when the link cannot be had", async () => {
        flags.procurement_docs = true;
        const tab = { opener: {}, location: { href: "" }, close: vi.fn() };
        vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
        download.mockResolvedValue({ error: { detail: "Not Found" } });
        renderChip(drafted);
        fireEvent.click(screen.getByRole("button"));
        await waitFor(() => expect(tab.close).toHaveBeenCalled());
    });
});
