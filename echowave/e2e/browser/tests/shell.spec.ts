import type { APIRequestContext, Page } from "@playwright/test";

import { API, apiAs, dismissPrompts, expect, marker, offScreen, signIn, test } from "./fixtures";

/**
 * The shell as a plain member (B) and the owner (A) meet it, at 390 px and
 * 1280 px: Chat's start screen, the first message, starters, the attach menu,
 * Recents, the phone's bottom bar, and an honest error state. Phase 3.
 *
 * Every test also fails on a console error, an uncaught page error or a 5xx
 * (``fixtures.ts``). The flag ``decibyl_private_threads`` decides where a
 * member's first message goes, and the checks follow whichever way it is set,
 * so the same file proves the flag-off behaviour too.
 */

const isPhone = (width: number | undefined) => (width ?? 1280) < 768;
const composer = (page: Page) => page.locator('textarea[aria-label^="Message"]').first();

async function features(api: APIRequestContext): Promise<Record<string, boolean>> {
    return (await api.get(`${API}/features`)).json();
}

async function openChat(page: Page) {
    await page.goto("/overview");
    await dismissPrompts(page);
    await expect(composer(page)).toBeVisible();
}

test("a member's first message from Chat's start screen is theirs and answered", async ({ page, playwright }) => {
    const b = await apiAs(playwright, "B");
    const a = await apiAs(playwright, "A");
    const privateThreads = Boolean((await features(b)).decibyl_private_threads);
    await signIn(page, "B");
    await openChat(page);

    // The start screen never opens on a failure. With private threads on a
    // member has nothing of their own here yet, so it greets; off, it is the
    // conversation everyone shares, as before.
    if (privateThreads) {
        await expect(page.getByText("Hi, I'm Decibyl!")).toBeVisible();
        // Nor is the owner's conversation measured beside B's empty box.
        await expect(page.getByLabel(/^Memory:/)).toHaveCount(0);
    }
    await expect(page.getByText("Could not load this conversation")).toHaveCount(0);

    const text = marker("first");
    await composer(page).fill(text);
    await composer(page).press("Enter");
    await expect(page.getByText(text).first()).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: /not found|could not send/i })).toHaveCount(0);

    if (privateThreads) await expect(page).toHaveURL(/thread=/);
    const thread = new URL(page.url()).searchParams.get("thread");
    if (privateThreads) {
        // A new conversation of B's own, on the address so a refresh keeps it.
        expect(thread, "the view switched to the new conversation").toMatch(/^[0-9a-f-]{36}$/);
        const mine = await b.get(`${API}/timeline?assistant=true&thread_id=${thread}`);
        expect(mine.status()).toBe(200);
        expect(JSON.stringify(await mine.json())).toContain(text);
        // The owner cannot read it, and it is not in their list.
        expect((await a.get(`${API}/timeline?assistant=true&thread_id=${thread}`)).status()).toBe(404);
        expect(JSON.stringify(await (await a.get(`${API}/timeline/threads?limit=200`)).json())).not.toContain(text);
        await page.reload();
        await expect(page.getByText(text).first()).toBeVisible();
    } else {
        // Flag off: the original conversation, exactly as before.
        expect(thread).toBeNull();
        expect(JSON.stringify(await (await b.get(`${API}/timeline?assistant=true`)).json())).toContain(text);
    }
    await a.dispose();
    await b.dispose();
});

test("someone else's conversation stays not found, on screen too", async ({ page, playwright }) => {
    const a = await apiAs(playwright, "A");
    test.skip(!(await features(a)).decibyl_private_threads, "flag decibyl_private_threads is off");
    const text = marker("owners");
    const threadId = crypto.randomUUID();
    expect((await a.post(`${API}/timeline/message`, { data: { assistant: true, thread_id: threadId, text } })).status()).toBe(200);
    await signIn(page, "B");
    await page.goto(`/overview?thread=${threadId}`);
    await dismissPrompts(page);
    await expect(page.getByText("Could not load this conversation")).toBeVisible();
    await expect(page.getByText(text)).toHaveCount(0);
    await a.dispose();
});

test("starters fill the box; the attach menu offers its four ways in", async ({ page, playwright }) => {
    const b = await apiAs(playwright, "B");
    test.skip(!(await features(b)).chat_shell, "flag chat_shell is off");
    await signIn(page, "B");
    await openChat(page);
    const starters = page.locator('[aria-label="Ask Decibyl"] button');
    await expect(starters.first()).toBeVisible();
    const count = await starters.count();
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThanOrEqual(3);
    const first = (await starters.first().innerText()).trim();
    let posted = false;
    page.on("request", (request) => {
        if (request.method() === "POST" && request.url().includes("/timeline/message")) posted = true;
    });
    await starters.first().click();
    await expect(composer(page)).toHaveValue(first);
    expect(posted, "a starter fills the box and sends nothing").toBe(false);

    await page.getByRole("button", { name: "Attach" }).click();
    for (const way of ["Files", "Voice note", "Meeting mode", "Paste notes"]) {
        await expect(page.getByRole("menuitem", { name: new RegExp(way) }).first()).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await b.dispose();
});

test("Recents shows a member their own conversations and not the owner's", async ({ page, playwright, viewport }) => {
    const a = await apiAs(playwright, "A");
    const b = await apiAs(playwright, "B");
    const flags = await features(b);
    const privateThreads = Boolean(flags.decibyl_private_threads);
    const owners = marker("arecent");
    const mine = marker("brecent");
    expect((await a.post(`${API}/timeline/message`, { data: { assistant: true, thread_id: crypto.randomUUID(), text: owners } })).status()).toBe(200);
    expect((await b.post(`${API}/timeline/message`, { data: { assistant: true, thread_id: crypto.randomUUID(), text: mine } })).status()).toBe(200);
    await signIn(page, "B");
    await openChat(page);
    if (isPhone(viewport?.width)) {
        // In the header with the phone shell on; behind Menu in the old bar.
        if (flags.shell_mobile) await page.getByTestId("mobile-header").getByRole("button").first().click();
        else await page.getByRole("link", { name: "Menu" }).or(page.getByRole("button", { name: "Menu" })).first().click();
    }
    const recents = page.getByTestId("v2-recents");
    await expect(recents.getByRole("link", { name: mine })).toBeVisible();
    if (privateThreads) await expect(recents.getByText(owners)).toHaveCount(0);
    await recents.getByRole("link", { name: mine }).click();
    await expect(page.getByText(mine).first()).toBeVisible();
    await a.dispose();
    await b.dispose();
});

test("a conversation that cannot load says so, with a way to try again", async ({ page }) => {
    await signIn(page, "A");
    // The read fails at the network; it is never drawn as the empty greeting.
    await page.route(/\/api\/v1\/timeline\?.*assistant=true/, (route) => route.abort("failed"));
    await page.goto("/overview");
    await dismissPrompts(page);
    await expect(page.getByText("Could not load this conversation")).toBeVisible();
    await expect(page.getByRole("button", { name: /try again/i })).toBeVisible();
    await expect(page.getByText("Hi, I'm Decibyl!")).toHaveCount(0);
    await page.unroute(/\/api\/v1\/timeline\?.*assistant=true/);
    await page.getByRole("button", { name: /try again/i }).click();
    await expect(page.getByText("Could not load this conversation")).toHaveCount(0);
});

test("the frame: Chat and Today first, profile reachable, nothing off the edge", async ({ page, viewport, playwright }) => {
    const b = await apiAs(playwright, "B");
    const flags = await features(b);
    await signIn(page, "B");
    await openChat(page);
    if (isPhone(viewport?.width)) {
        test.skip(!flags.shell_mobile, "flag shell_mobile is off");
        const bar = page.getByTestId("mobile-tab-bar");
        await expect(bar).toBeVisible();
        const box = await bar.boundingBox();
        expect(box && box.y + box.height, "the bar sits at the bottom").toBeGreaterThan((viewport?.height ?? 844) - 4);
        await expect(bar.getByRole("link").nth(0)).toHaveText(/Chat/);
        await expect(bar.getByRole("link").nth(1)).toHaveText(/Today/);
        // The profile is in the header on a phone.
        await expect(page.getByTestId("mobile-header")).toBeVisible();
    } else {
        const nav = page.getByRole("navigation", { name: "Homes" });
        await expect(nav.getByRole("link").nth(0)).toHaveText(/Chat/);
        await expect(nav.getByRole("link").nth(1)).toHaveText(/Today/);
    }
    expect(await offScreen(page)).toEqual([]);
    // New chat opens an empty conversation of the person's own.
    await page.getByRole("button", { name: "New chat" }).click();
    await expect(page).toHaveURL(/thread=[0-9a-f-]{36}/);
    await expect(composer(page)).toBeVisible();
    expect(await offScreen(page)).toEqual([]);
    await b.dispose();
});

test("sources open beside the reply and close back to where you were", async ({ page, playwright }) => {
    const b = await apiAs(playwright, "B");
    test.skip(!(await features(b)).chat_shell, "flag chat_shell is off");
    await signIn(page, "B");
    await openChat(page);
    const text = marker("sources");
    await composer(page).fill(text);
    await composer(page).press("Enter");
    // By its own id: a Recents chip for a chat named "...sources..." is a
    // button called Sources too.
    const open = page.getByTestId("open-sources").last();
    await expect(open).toBeVisible({ timeout: 90_000 });
    await open.click();
    const panel = page.getByTestId("auxiliary-panel");
    await expect(panel).toBeVisible();
    expect(await offScreen(page)).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(open).toBeFocused();
    await b.dispose();
});
