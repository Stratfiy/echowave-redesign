/**
 * The unsaved-changes guard, and the two ways it used to let edits vanish.
 *
 * **The tab close.** The provider intercepted in-app link clicks and browser
 * back/forward, and nothing else. Closing the tab, reloading, or typing a new
 * address fires neither of those, so the one case with no undo — the window is
 * gone — was also the one case with no prompt. `beforeunload` is asserted here
 * by dispatching the event and reading `defaultPrevented`, because that flag,
 * not any message, is what makes the browser ask.
 *
 * **The throw.** `useUnsavedChanges` used to throw outside a provider. The
 * sections that hold edits are shared across screens, so adding the guard to
 * one could white-screen another page that had not been wrapped yet — which
 * makes the safe change the dangerous one. It degrades to a no-op instead, and
 * that is asserted rather than assumed: a regression here is silent on the
 * wrapped pages and fatal on the unwrapped ones.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { describe, expect, it } from "vitest";

import {
    UnsavedChangesProvider,
    useUnsavedChanges,
} from "../UnsavedChangesContext";

/** Registers a fixed dirty state, so each test states its own precondition. */
function Section({ dirty }: { dirty: boolean }) {
    useUnsavedChanges("test-section", dirty);
    return <p>section</p>;
}

/** Dispatches a cancelable `beforeunload` and reports whether it was blocked. */
function fireBeforeUnload(): boolean {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
}

describe("UnsavedChangesProvider", () => {
    it("lets the tab close when nothing is dirty", () => {
        render(
            <UnsavedChangesProvider>
                <Section dirty={false} />
            </UnsavedChangesProvider>,
        );

        expect(fireBeforeUnload()).toBe(false);
    });

    it("blocks the tab close while a section is dirty", () => {
        render(
            <UnsavedChangesProvider>
                <Section dirty />
            </UnsavedChangesProvider>,
        );

        expect(fireBeforeUnload()).toBe(true);
    });

    it("stops blocking once the section reports itself clean again", () => {
        // Saving does not unmount the form, it flips the flag. If the guard
        // only ever read the value it mounted with, every page would warn
        // forever after the first edit and operators would learn to click
        // through the dialog without reading it.
        function Toggling() {
            const [dirty, setDirty] = useState(true);
            return (
                <>
                    <Section dirty={dirty} />
                    <button onClick={() => setDirty(false)}>save</button>
                </>
            );
        }

        render(
            <UnsavedChangesProvider>
                <Toggling />
            </UnsavedChangesProvider>,
        );

        expect(fireBeforeUnload()).toBe(true);
        fireEvent.click(screen.getByText("save"));
        expect(fireBeforeUnload()).toBe(false);
    });

    it("stops blocking when the dirty section unmounts", () => {
        function Unmounting() {
            const [shown, setShown] = useState(true);
            return (
                <>
                    {shown && <Section dirty />}
                    <button onClick={() => setShown(false)}>close</button>
                </>
            );
        }

        render(
            <UnsavedChangesProvider>
                <Unmounting />
            </UnsavedChangesProvider>,
        );

        expect(fireBeforeUnload()).toBe(true);
        fireEvent.click(screen.getByText("close"));
        expect(fireBeforeUnload()).toBe(false);
    });
});

describe("useUnsavedChanges outside a provider", () => {
    it("renders instead of throwing, and guards nothing", () => {
        expect(() => render(<Section dirty />)).not.toThrow();
        expect(screen.getByText("section")).toBeDefined();
        // No provider means no guard — asserted so this reads as the
        // deliberate trade-off it is, not an accident.
        expect(fireBeforeUnload()).toBe(false);
    });
});

/**
 * The shape every guarded form on this app now uses: a serialized snapshot of
 * the fields, compared against a baseline taken when the server's values
 * landed and moved again on each successful save.
 *
 * Asserted here rather than per-screen because the rule is the same on all of
 * them and the failure is identical — a baseline that is never moved leaves a
 * saved form warning forever, and a baseline captured before the load lands
 * makes the form dirty the moment it populates itself. Both train people to
 * click through the dialog, which costs more than having no dialog.
 */
describe("the baseline/snapshot rule", () => {
    function Form({ loaded }: { loaded: string }) {
        const [value, setValue] = useState(loaded);
        const [baseline, setBaseline] = useState<string | null>(null);

        const snapshot = JSON.stringify([value]);

        // Stands in for "the fetch resolved and the setters flushed".
        useEffect(() => {
            if (baseline === null) setBaseline(snapshot);
        }, [baseline, snapshot]);

        const dirty = baseline !== null && snapshot !== baseline;
        useUnsavedChanges("form", dirty);

        return (
            <>
                <button onClick={() => setValue("edited")}>edit</button>
                <button onClick={() => setValue(loaded)}>undo</button>
                <button onClick={() => setBaseline(snapshot)}>save</button>
            </>
        );
    }

    function renderForm() {
        render(
            <UnsavedChangesProvider>
                <Form loaded="from-server" />
            </UnsavedChangesProvider>,
        );
    }

    it("is clean once populated from the server", () => {
        renderForm();
        expect(fireBeforeUnload()).toBe(false);
    });

    it("is dirty after an edit", () => {
        renderForm();
        fireEvent.click(screen.getByText("edit"));
        expect(fireBeforeUnload()).toBe(true);
    });

    it("is clean again when the edit is undone by hand", () => {
        // The reason dirty compares against stored values rather than
        // tracking whether a control was touched.
        renderForm();
        fireEvent.click(screen.getByText("edit"));
        fireEvent.click(screen.getByText("undo"));
        expect(fireBeforeUnload()).toBe(false);
    });

    it("is clean after a save moves the baseline", () => {
        renderForm();
        fireEvent.click(screen.getByText("edit"));
        expect(fireBeforeUnload()).toBe(true);
        fireEvent.click(screen.getByText("save"));
        expect(fireBeforeUnload()).toBe(false);
    });
});
