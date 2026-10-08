/**
 * Files takes a drop anywhere on the page: every file in it is added, one
 * after another, through the same upload the dialog uses. A file that cannot
 * be read is named and skipped, not silently lost.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const upload = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false, redirectToLogin: vi.fn() }) }));
vi.mock("@/lib/uploadKnowledge", async (actual) => ({
    ...(await actual<typeof import("@/lib/uploadKnowledge")>()),
    uploadKnowledge: upload,
}));
vi.mock("../DocumentList", () => ({ default: () => <div>list</div> }));
vi.mock("../DocumentUpload", () => ({ default: () => <div>dialog</div> }));
vi.mock("@/components/layout/PageHeader", () => ({
    PageHeader: ({ title, description, actions }: { title: React.ReactNode; description: React.ReactNode; actions: React.ReactNode }) => (
        <header>
            <h1>{title}</h1>
            <p>{description}</p>
            {actions}
        </header>
    ),
    PageBody: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

import FilesPage from "../page";

function drop(target: Element, files: File[]) {
    const dataTransfer = { types: ["Files"], files };
    fireEvent.dragEnter(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
}

beforeEach(() => {
    upload.mockReset();
    upload.mockResolvedValue({});
    toast.success.mockReset();
    toast.error.mockReset();
});

describe("Files", () => {
    it("is called Files", () => {
        render(<FilesPage />);
        expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Files");
    });

    it("shows where to drop while a file is dragged over the page", () => {
        render(<FilesPage />);
        const zone = screen.getByTestId("files-drop-zone");
        fireEvent.dragEnter(zone, { dataTransfer: { types: ["Files"], files: [] } });
        expect(screen.getByText("Drop to add to Files")).toBeTruthy();
    });

    it("adds every dropped file for every agent, and names the one it cannot read", async () => {
        render(<FilesPage />);
        const a = new File(["a"], "prices.pdf", { type: "application/pdf" });
        const b = new File(["b"], "faq.txt", { type: "text/plain" });
        const bad = new File(["c"], "photo.exe", { type: "application/octet-stream" });
        drop(screen.getByTestId("files-drop-zone"), [a, bad, b]);
        await waitFor(() => expect(toast.success).toHaveBeenCalledWith("2 files added. Reading them now."));
        expect(upload).toHaveBeenCalledTimes(2);
        expect(upload.mock.calls.map((call) => call[0].name)).toEqual(["prices.pdf", "faq.txt"]);
        expect(upload.mock.calls[0][1]).toEqual({ scope: "org" });
        expect(toast.error.mock.calls[0][0]).toMatch(/^photo\.exe: /);
    });
});
