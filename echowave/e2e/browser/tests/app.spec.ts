import type { APIRequestContext } from "@playwright/test";

import { API, FAILURE_REPLY, REQUIRE_MODEL, dismissPrompts, expect, marker, offScreen, signIn, test } from "./fixtures";

/**
 * The app as a person meets it, at 390 px (a phone) and 1280 px (a laptop).
 * Every test also fails on a console error, an uncaught page error or a 5xx
 * (see ``fixtures.ts``), and leaves a screenshot and a trace when it fails.
 */

const SECTION_PAUSE_MS = Number(process.env.E2E_SECTION_PAUSE_MS || 3000);

const isPhone = (width: number | undefined) => (width ?? 1280) < 768;

type Event = { id: number; actor: string; kind: string; payload?: { body?: string; failed?: boolean } };

/** Decibyl's answer to the message carrying ``text``, if it has one yet. */
async function replyTo(api: APIRequestContext, text: string) {
    const threads = await (await api.get(`${API}/timeline/threads?limit=10`)).json();
    for (const thread of threads.threads ?? []) {
        const query = thread.thread_id ? `&thread_id=${encodeURIComponent(thread.thread_id)}` : "";
        const events: Event[] = (await (await api.get(`${API}/timeline?assistant=true${query}`)).json()).events ?? [];
        const asked = events.find((e) => e.actor === "human" && String(e.payload?.body ?? "").includes(text));
        if (!asked) continue;
        return events
            .filter((e) => e.actor === "agent" && e.kind === "message" && e.id > asked.id)
            .sort((x, y) => x.id - y.id)[0]?.payload;
    }
    return undefined;
}

test("sign in lands in the app", async ({ page }) => {
    await signIn(page);
    expect(new URL(page.url()).pathname).not.toMatch(/^\/auth\//);
    await expect(page.locator('textarea[aria-label^="Message"]').first()).toBeVisible();
});

test("Chat and Today are the primary destinations", async ({ signedIn: page, viewport }) => {
    const nav = isPhone(viewport?.width) ? page.getByTestId("mobile-tab-bar") : page.getByRole("navigation").first();
    await expect(nav).toBeVisible();
    const items = (await nav.locator("a, button").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
    expect(items.slice(0, 2), `primary navigation reads ${JSON.stringify(items)}`).toEqual(["Chat", "Today"]);
    await nav.getByRole("link", { name: "Today" }).click();
    // Today lives at /tasks (SectionTabs); the address is not the point.
    await expect(page).toHaveURL(/\/(today|tasks)/);
    await expect(page.getByRole("heading", { name: /today/i }).first()).toBeVisible();
    await nav.getByRole("link", { name: "Chat" }).click();
    await expect(page.locator('textarea[aria-label^="Message"]').first()).toBeVisible();
});

test("send a message and see the reply appear", async ({ signedIn: page, api }) => {
    test.setTimeout(240_000);
    const text = marker("chat");
    const box = page.locator('textarea[aria-label^="Message"]').first();
    await box.fill(`${text}: reply with one short sentence.`);
    await box.press("Enter");
    await expect(page.getByText(text).first()).toBeVisible();

    // The reply the server holds for that message, in whichever thread the
    // composer put it (a new chat has an id; the main thread has none).
    let reply: { body?: string; failed?: boolean } | undefined;
    await expect
        .poll(async () => {
            reply = await replyTo(api, text);
            return Boolean(reply?.body);
        }, { timeout: 150_000, intervals: [2_000] })
        .toBe(true);

    // The reply the server has is the reply on the screen.
    const firstWords = String(reply!.body).split("\n")[0].slice(0, 40);
    await expect(page.getByText(firstWords, { exact: false }).first()).toBeVisible();
    const couldNot = reply!.failed || String(reply!.body).startsWith(FAILURE_REPLY);
    if (couldNot && !REQUIRE_MODEL) {
        test.skip(true, "requires a model: no provider key here, so the reply on screen is the could-not-reach-a-model apology");
    }
    expect(couldNot, `Decibyl could not reach a model: ${reply!.body}`).toBe(false);
});

test("open a thread from Recents", async ({ signedIn: page, api, viewport }) => {
    const text = marker("recent");
    const threadId = crypto.randomUUID();
    const sent = await api.post(`${API}/timeline/message`, { data: { assistant: true, thread_id: threadId, text } });
    expect(sent.status()).toBe(200);
    await page.goto("/overview");
    await dismissPrompts(page);
    if (isPhone(viewport?.width)) {
        // On a phone, Recents lives behind the menu in the header.
        await page.getByTestId("mobile-header").getByRole("button").first().click();
    }
    const recents = page.getByTestId("v2-recents");
    await expect(recents).toBeVisible();
    await recents.getByRole("link", { name: text }).click();
    await expect(page.getByText(text).first()).toBeVisible();
    await expect(page.locator('textarea[aria-label^="Message"]').first()).toBeVisible();
});

test("confirm an action card: it shows what will happen and runs once", async ({ signedIn: page, api }) => {
    const features = await (await api.get(`${API}/features`)).json();
    test.skip(!features.saved_items, "flag saved_items is off");
    const title = marker("card");
    const created = await api.post(`${API}/me/saved`, { data: { title, kind: "note", body: title, visibility: "private" } });
    expect(created.status()).toBe(200);
    const item = await created.json();

    await page.goto("/settings/saved");
    await dismissPrompts(page);
    await page.getByTestId("saved-list").getByRole("button", { name: new RegExp(title) }).click();
    await page.getByTestId("saved-preview").getByRole("button", { name: "Delete" }).click();

    const card = page.getByTestId("settings-card");
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-state", "proposed");
    await expect(card.getByTestId("action-preview")).toContainText(/delete/i);
    const approve = card.getByTestId("action-preview").getByRole("button", { name: /approve/i });
    // Two quick clicks: the second must not start a second run.
    await approve.click();
    await approve.click({ timeout: 2_000 }).catch(() => undefined);
    await expect(card).toHaveAttribute("data-state", "done", { timeout: 90_000 });

    const listed = await (await api.get(`${API}/me/saved?scope=personal`)).json();
    expect(listed.items.map((i: { id: number }) => i.id)).not.toContain(item.id);
    const events = await (await api.get(`${API}/me/saved/${item.id}`)).json();
    expect(events.status).toBe("deleted");
});

test("Settings opens and nothing runs off the screen edge", async ({ signedIn: page, viewport }) => {
    if (isPhone(viewport?.width)) {
        await page.getByTestId("header-profile").click();
    } else {
        await page.getByRole("button", { name: "Account menu" }).click();
    }
    await page.getByRole("menuitem", { name: /settings/i }).first().click();
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.getByRole("heading").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await offScreen(page), "Settings fits the screen").toEqual([]);

    // Every section reachable from Settings, not only its front page.
    const links = page.locator('a[href^="/settings/"]');
    await expect(links.first()).toBeAttached();
    const sections = await links.evaluateAll((all) => [...new Set(all.map((a) => a.getAttribute("href") || ""))].filter(Boolean));
    expect(sections.length, "Settings lists its sections").toBeGreaterThan(3);
    for (const href of sections.slice(0, 25)) {
        // A person's pace, not a crawler's: each screen makes a few dozen
        // requests, and the API allows 600 a minute per address.
        await page.waitForTimeout(SECTION_PAUSE_MS);
        await page.goto(href);
        await page.waitForLoadState("networkidle");
        expect(await offScreen(page), `${href} fits the screen`).toEqual([]);
    }
});

test("Chat and Today fit the screen", async ({ signedIn: page }) => {
    await page.waitForLoadState("networkidle");
    expect(await offScreen(page), "Chat fits the screen").toEqual([]);
    await page.goto("/tasks");
    await page.waitForLoadState("networkidle");
    expect(await offScreen(page), "Today fits the screen").toEqual([]);
});
