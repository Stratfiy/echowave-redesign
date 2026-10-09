/**
 * Folders on the Files page, driven the way a person does it: open one and
 * see where you are, drop files onto one, drop a whole desktop folder and
 * keep its structure, drag a file onto a folder to move it, and be asked
 * before a folder that holds something is deleted.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
    listFileFoldersApiV1KnowledgeBaseFileFoldersGet: vi.fn(),
    ensureFileFolderPathApiV1KnowledgeBaseFileFoldersEnsurePost: vi.fn(),
    updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch: vi.fn(),
    createFileFolderApiV1KnowledgeBaseFileFoldersPost: vi.fn(),
    deleteFileFolderApiV1KnowledgeBaseFileFoldersFolderIdDelete: vi.fn(),
    updateFileFolderApiV1KnowledgeBaseFileFoldersFolderIdPatch: vi.fn(),
}));
const upload = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
const listProps = vi.hoisted(() => ({ last: null as null | { fileFolderId?: number | null } }));

vi.mock("@/client/sdk.gen", () => sdk);
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false, redirectToLogin: vi.fn() }) }));
vi.mock("@/lib/uploadKnowledge", async (actual) => ({
    ...(await actual<typeof import("@/lib/uploadKnowledge")>()),
    uploadKnowledge: upload,
}));
vi.mock("../DocumentList", () => ({
    default: (props: { fileFolderId?: number | null }) => {
        listProps.last = props;
        return <div>list</div>;
    },
}));
vi.mock("../DocumentUpload", () => ({ default: () => <div>dialog</div> }));
vi.mock("@/components/layout/PageHeader", () => ({
    PageHeader: ({ title }: { title: React.ReactNode }) => <h1>{title}</h1>,
    PageBody: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

import { FILE_DRAG_TYPE } from "../fileFolders";
import FilesPage from "../page";

const FOLDERS = [
    { id: 1, folder_uuid: "a", name: "Pricing", parent_id: null, path: "Pricing", file_count: 2, folder_count: 1 },
    { id: 2, folder_uuid: "b", name: "2026", parent_id: 1, path: "Pricing/2026", file_count: 0, folder_count: 0 },
    { id: 3, folder_uuid: "c", name: "Menus", parent_id: null, path: "Menus", file_count: 0, folder_count: 0 },
];

beforeEach(() => {
    for (const fn of Object.values(sdk)) fn.mockReset();
    sdk.listFileFoldersApiV1KnowledgeBaseFileFoldersGet.mockResolvedValue({ data: { folders: FOLDERS } });
    upload.mockReset();
    upload.mockResolvedValue({});
    toast.success.mockReset();
    toast.error.mockReset();
});

describe("folders on the Files page", () => {
    it("opens a folder and shows the way back", async () => {
        render(<FilesPage />);
        fireEvent.click(await screen.findByText("Pricing"));
        const path = screen.getByRole("navigation", { name: "Folder path" });
        expect(within(path).getByText("All files")).toBeTruthy();
        expect(within(path).getByText("Pricing").getAttribute("aria-current")).toBe("page");
        expect(listProps.last?.fileFolderId).toBe(1);
        // Inside Pricing, its own folder is shown.
        expect(screen.getByText("2026")).toBeTruthy();
    });

    it("uploads files dropped from the desktop onto a folder into that folder", async () => {
        render(<FilesPage />);
        const tile = (await screen.findByText("Menus")).closest("li")!;
        const file = new File(["x"], "menu.pdf", { type: "application/pdf" });
        fireEvent.drop(tile, { dataTransfer: { types: ["Files"], files: [file] } });
        await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
        expect(upload.mock.calls[0][0].name).toBe("menu.pdf");
        expect(upload.mock.calls[0][2]).toEqual({ fileFolderId: 3 });
    });

    it("keeps a dropped desktop folder's structure", async () => {
        sdk.ensureFileFolderPathApiV1KnowledgeBaseFileFoldersEnsurePost.mockResolvedValue({ data: { id: 42 } });
        render(<FilesPage />);
        await screen.findByText("Pricing");
        const entry = (name: string) => ({ isFile: true, isDirectory: false, name, file: (ok: (f: File) => void) => ok(new File(["x"], name)) });
        const dir = {
            isFile: false,
            isDirectory: true,
            name: "Contracts",
            createReader: () => {
                const batches = [[entry("acme.pdf"), entry("beta.pdf")], []];
                return { readEntries: (ok: (e: unknown[]) => void) => ok(batches.shift() ?? []) };
            },
        };
        fireEvent.drop(screen.getByTestId("files-drop-zone"), {
            dataTransfer: { types: ["Files"], files: [], items: [{ kind: "file", webkitGetAsEntry: () => dir }] },
        });
        await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
        // The folder is made (or found) once, under where it was dropped.
        expect(sdk.ensureFileFolderPathApiV1KnowledgeBaseFileFoldersEnsurePost).toHaveBeenCalledTimes(1);
        expect(sdk.ensureFileFolderPathApiV1KnowledgeBaseFileFoldersEnsurePost).toHaveBeenCalledWith({
            body: { parent_id: null, path: ["Contracts"] },
        });
        expect(upload.mock.calls.map((call) => call[2])).toEqual([{ fileFolderId: 42 }, { fileFolderId: 42 }]);
    });

    it("moves a file dragged onto a folder", async () => {
        sdk.updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch.mockResolvedValue({ data: { filename: "rates.pdf" } });
        render(<FilesPage />);
        const tile = (await screen.findByText("Pricing")).closest("li")!;
        fireEvent.drop(tile, {
            dataTransfer: { types: [FILE_DRAG_TYPE, "text/plain"], getData: (type: string) => (type === FILE_DRAG_TYPE ? "doc-1" : "") },
        });
        await waitFor(() =>
            expect(sdk.updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch).toHaveBeenCalledWith({
                path: { document_uuid: "doc-1" },
                body: { file_folder_id: 1 },
            }),
        );
        expect(upload).not.toHaveBeenCalled();
        await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Moved "rates.pdf" to Pricing'));
    });

    it("asks what to do with what a folder holds before deleting it", async () => {
        sdk.deleteFileFolderApiV1KnowledgeBaseFileFoldersFolderIdDelete.mockResolvedValue({ data: {} });
        render(<FilesPage />);
        await screen.findByText("Pricing");
        const more = screen.getByRole("button", { name: "More for Pricing" });
        fireEvent.keyDown(more, { key: "Enter" });
        fireEvent.click(await screen.findByText("Delete"));
        expect(await screen.findByText(/It holds 2 files and 1 folder/)).toBeTruthy();
        expect(sdk.deleteFileFolderApiV1KnowledgeBaseFileFoldersFolderIdDelete).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Move them, delete the folder" }));
        await waitFor(() =>
            expect(sdk.deleteFileFolderApiV1KnowledgeBaseFileFoldersFolderIdDelete).toHaveBeenCalledWith({
                path: { folder_id: 1 },
                query: { contents: "move_to_parent" },
            }),
        );
    });

    it("makes a new folder where you are", async () => {
        sdk.createFileFolderApiV1KnowledgeBaseFileFoldersPost.mockResolvedValue({ data: { id: 9 } });
        render(<FilesPage />);
        fireEvent.click(await screen.findByText("Pricing"));
        fireEvent.click(screen.getByRole("button", { name: "New folder" }));
        fireEvent.change(await screen.findByLabelText("Folder name"), { target: { value: "Bills" } });
        fireEvent.click(screen.getByRole("button", { name: "Create folder" }));
        await waitFor(() =>
            expect(sdk.createFileFolderApiV1KnowledgeBaseFileFoldersPost).toHaveBeenCalledWith({ body: { name: "Bills", parent_id: 1 } }),
        );
    });
});
