/**
 * People (PEOPLE.md): the list's honest states, the connect chip in place,
 * the phone's contact picker only where it exists, and duplicates merged
 * only on the person's word.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PeopleStatus, ProviderStatus } from "@/client/types.gen";
import { ago, channelLabel, syncedLine } from "@/lib/people/format";
import { contactPicker } from "@/lib/people/picker";

import { PeopleList } from "../PeopleList";
import { PeopleSources } from "../PeopleSources";

const api = vi.hoisted(() => ({
    list: vi.fn(),
    status: vi.fn(),
    merges: vi.fn(),
    settings: vi.fn(),
    connect: vi.fn(),
    sync: vi.fn(),
    importFile: vi.fn(),
    importPicked: vi.fn(),
    decide: vi.fn(),
}));
vi.mock("@/client/sdk.gen", () => ({
    myPeopleApiV1PeopleGet: api.list,
    peopleStatusApiV1PeopleStatusGet: api.status,
    myMergesApiV1PeopleMergesGet: api.merges,
    savePeopleSettingsApiV1PeopleSettingsPut: api.settings,
    connectProviderApiV1PeopleConnectProviderPost: api.connect,
    startSyncApiV1PeopleSyncProviderPost: api.sync,
    importPeopleApiV1PeopleImportPost: api.importFile,
    importPickedApiV1PeopleImportPickerPost: api.importPicked,
    decideMergeApiV1PeopleMergesMergeIdPost: api.decide,
}));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { id: 1 }, loading: false }),
}));

globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
} as unknown as typeof ResizeObserver;

function provider(overrides: Partial<ProviderStatus> = {}): ProviderStatus {
    return {
        provider: "google",
        name: "Google Contacts",
        toolkit: "gmail",
        state: "ok",
        detail: null,
        last_synced_at: null,
        counts: {},
        ...overrides,
    };
}

function status(overrides: Partial<PeopleStatus> = {}): PeopleStatus {
    return {
        providers: [
            provider(),
            provider({
                provider: "microsoft",
                name: "Outlook contacts",
                toolkit: "outlook",
                state: "not_connected",
            }),
        ],
        total: 0,
        open_merges: 0,
        agents_may_read: false,
        ...overrides,
    };
}

const person = {
    id: "p1",
    name: "Ravi Kumar",
    company: "Kumar Traders",
    phones: ["+919876543210"],
    emails: [],
    sources: ["google"],
    brief: "Rice supplier; October order open.",
    last_interaction_at: new Date().toISOString(),
};

beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    api.merges.mockResolvedValue({ data: { merges: [] } });
    api.status.mockResolvedValue({ data: status() });
});

afterEach(() => {
    delete (navigator as Navigator & { contacts?: unknown }).contacts;
});

describe("the People list", () => {
    it("shows the contacts with their brief, newest first", async () => {
        api.list.mockResolvedValue({
            data: { people: [person], total: 1, shared: [] },
        });
        render(<PeopleList />);
        expect(await screen.findByText("Ravi Kumar")).toBeTruthy();
        expect(screen.getByText("Rice supplier; October order open.")).toBeTruthy();
        expect(screen.getByText("Only you can see these.", { exact: false })).toBeTruthy();
    });

    it('says how to get contacts when there are none, not "no data"', async () => {
        api.list.mockResolvedValue({ data: { people: [], total: 0, shared: [] } });
        render(<PeopleList />);
        expect(await screen.findByText("No contacts yet")).toBeTruthy();
        expect(screen.getByText(/Connect Google or Outlook, import a file/)).toBeTruthy();
    });

    it("a failed load is an error with Retry, never an empty list", async () => {
        api.list.mockResolvedValue({
            error: { detail: "boom" },
            response: { status: 500 },
        });
        render(<PeopleList />);
        expect(await screen.findByTestId("error-state")).toBeTruthy();
        expect(screen.queryByText("No contacts yet")).toBeNull();
    });

    it("a search that matches nobody says so, in different words from empty", async () => {
        api.list.mockResolvedValueOnce({
            data: { people: [person], total: 1, shared: [] },
        });
        api.list.mockResolvedValue({ data: { people: [], total: 1, shared: [] } });
        render(<PeopleList />);
        await screen.findByText("Ravi Kumar");
        fireEvent.change(screen.getByTestId("people-search"), {
            target: { value: "zzz" },
        });
        expect(await screen.findByText("Nobody matches “zzz”")).toBeTruthy();
        expect(api.list).toHaveBeenLastCalledWith({ query: { q: "zzz" } });
    });

    it("possible duplicates wait for the person", async () => {
        api.list.mockResolvedValue({
            data: { people: [person], total: 1, shared: [] },
        });
        api.merges.mockResolvedValue({
            data: {
                merges: [
                    {
                        id: "m1",
                        reason: "phone",
                        value: "+919876543210",
                        keep: person,
                        other: { ...person, id: "p2", name: "Ravi K" },
                    },
                ],
            },
        });
        api.decide.mockResolvedValue({ data: person });
        render(<PeopleList />);
        expect(await screen.findByText("1 possible duplicate")).toBeTruthy();
        expect(screen.getByText("Nothing is merged until you say so.")).toBeTruthy();
        fireEvent.click(screen.getByText("Keep both"));
        await waitFor(() =>
            expect(api.decide).toHaveBeenCalledWith({
                path: { merge_id: "m1" },
                body: { action: "keep_both" },
            }),
        );
    });
});

describe("sources", () => {
    it("a missing connection is a connect chip in place", async () => {
        const open = vi.spyOn(window, "open").mockReturnValue(null);
        api.connect.mockResolvedValue({
            data: { provider: "microsoft", connect_url: "https://connect.example/x" },
        });
        api.sync.mockResolvedValue({
            data: { provider: "microsoft", state: "syncing" },
        });
        const changed = vi.fn();
        render(<PeopleSources providers={status().providers} onChanged={changed} />);
        fireEvent.click(screen.getByTestId("people-connect-microsoft"));
        await waitFor(() => expect(open).toHaveBeenCalledWith("https://connect.example/x", "_blank", "noopener,noreferrer"));
        fireEvent.click(await screen.findByText("I've signed in"));
        await waitFor(() =>
            expect(api.sync).toHaveBeenCalledWith({
                path: { provider: "microsoft" },
            }),
        );
        expect(changed).toHaveBeenCalled();
        open.mockRestore();
    });

    it("syncing, needs setup and error each say what they are", () => {
        render(
            <PeopleSources
                providers={[
                    provider({ state: "syncing" }),
                    provider({
                        provider: "microsoft",
                        state: "error",
                        detail: "The connection does not allow reading contacts.",
                    }),
                ]}
                onChanged={() => {}}
            />,
        );
        expect(screen.getByText("Syncing your contacts…")).toBeTruthy();
        expect(screen.getByText("The connection does not allow reading contacts.")).toBeTruthy();
        expect(screen.getByText("Try again")).toBeTruthy();
    });

    it("the phone picker is hidden where the browser has none", () => {
        render(<PeopleSources providers={[]} onChanged={() => {}} />);
        expect(screen.queryByTestId("people-picker")).toBeNull();
    });

    it("and offered on Android Chrome, sending what was picked", async () => {
        const select = vi.fn().mockResolvedValue([{ name: ["Anil"], tel: ["99887 76655"] }]);
        (navigator as Navigator & { contacts?: unknown }).contacts = {
            select,
            getProperties: async () => ["name", "tel"],
        };
        api.importPicked.mockResolvedValue({
            data: {
                source: "picker",
                added: 1,
                updated: 0,
                unchanged: 0,
                skipped: 0,
                open_merges: 0,
            },
        });
        render(<PeopleSources providers={[]} onChanged={() => {}} />);
        fireEvent.click(screen.getByTestId("people-picker"));
        await waitFor(() => expect(select).toHaveBeenCalledWith(["name", "tel"], { multiple: true }));
        await waitFor(() =>
            expect(api.importPicked).toHaveBeenCalledWith({
                body: {
                    contacts: [{ name: ["Anil"], tel: ["99887 76655"], email: [] }],
                },
            }),
        );
        expect(await screen.findByText("1 added.")).toBeTruthy();
    });
});

describe("words", () => {
    it("format", () => {
        const now = new Date("2026-10-09T12:00:00Z");
        expect(ago("2026-10-09T11:58:00Z", now)).toBe("2 min ago");
        expect(ago("2026-10-08T12:00:00Z", now)).toBe("yesterday");
        expect(channelLabel("whatsapp")).toBe("WhatsApp");
        expect(channelLabel("fax")).toBe("fax");
        expect(syncedLine({ added: 3, removed: 1 })).toBe("3 added, 1 removed");
        expect(syncedLine({})).toBe("no changes");
        expect(contactPicker(undefined)).toBeNull();
    });
});
