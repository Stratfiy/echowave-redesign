import { API, apiAs, dismissPrompts, expect, offScreen, signIn, test } from "./fixtures";

/**
 * The shell's doors and edges, phase 3: early access (screen 01), old links
 * to the new places, and the workflow editor kept reachable -- a read-only
 * step list on a phone (screen 45). Each check follows its flag and says so
 * when it is off.
 */

const isPhone = (width: number | undefined) => (width ?? 1280) < 768;

test("early access: join the list once, and a bad invitation says what it is", async ({ page, playwright }) => {
    const anonymous = await playwright.request.newContext();
    const languages = await anonymous.get(`${API}/public/early-access/languages`);
    test.skip(languages.status() === 404, "flag early_access is off");
    await anonymous.dispose();

    const email = `wait-${Date.now().toString(36)}@decibyl-local.com`;
    await page.goto("/early-access");
    await page.locator('input[type="email"]').fill(email);
    await page.locator('button[type="submit"]').click();
    await expect(page.getByTestId("waitlist-result")).toHaveAttribute("data-result", "waitlisted");
    expect(await offScreen(page)).toEqual([]);

    // The second time is said honestly, not as a new request.
    await page.goto("/early-access");
    await page.locator('input[type="email"]').fill(email);
    await page.locator('button[type="submit"]').click();
    await expect(page.getByTestId("waitlist-result")).toHaveAttribute("data-result", "already_on_list");

    await page.goto("/invite/NOT-A-REAL-CODE");
    await expect(page.getByTestId("invitation")).toHaveAttribute("data-state", "invalid");
    expect(await offScreen(page)).toEqual([]);
});

test("old links reach the new places", async ({ page, playwright }) => {
    const b = await apiAs(playwright, "B");
    const threads = await (await b.get(`${API}/timeline/threads?limit=1`)).json();
    const own = threads.threads.find((t: { thread_id: string | null }) => t.thread_id)?.thread_id as string | undefined;
    await signIn(page, "B");
    for (const [from, to] of [
        ["/chat", /\/overview$/],
        ["/home", /\/overview$/],
        ["/assistant", /\/overview$/],
        ["/today", /\/tasks/],
    ] as const) {
        await page.goto(from);
        await expect(page, `${from} goes to its new place`).toHaveURL(to);
        // Arrive before leaving again, as a person does: leaving mid-load
        // cuts the page's own requests short.
        await page.waitForLoadState("networkidle");
    }
    if (own) {
        await page.goto(`/chat/${own}`);
        await expect(page).toHaveURL(new RegExp(`/overview\\?thread=${own}`));
        await dismissPrompts(page);
        await expect(page.getByText("Could not load this conversation")).toHaveCount(0);
    }
    await b.dispose();
});

test("the workflow editor stays reachable; a phone gets the steps to read", async ({ page, playwright, viewport }) => {
    const a = await apiAs(playwright, "A");
    const flags = await (await a.get(`${API}/features`)).json();
    const workflows = await (await a.get(`${API}/workflow/fetch`)).json();
    test.skip(!Array.isArray(workflows) || workflows.length === 0, "no agent on this account to open");
    const id = workflows[0].id;
    await signIn(page, "A");
    await page.goto(`/workflow/${id}`);
    await dismissPrompts(page);
    if (isPhone(viewport?.width) && flags.shell_mobile) {
        // The steps as a list, with the full editor one tap away.
        await expect(page.getByText(workflows[0].name).first()).toBeVisible();
        await expect(page.getByRole("button", { name: /editor/i }).first()).toBeVisible();
    } else {
        // Edit opens on the chat editor; the canvas is one tab over.
        await page.getByRole("tab", { name: "Graph" }).or(page.getByRole("button", { name: "Graph" })).first().click();
        await expect(page.locator(".react-flow").first()).toBeVisible();
    }
    expect(await offScreen(page)).toEqual([]);
    await a.dispose();
});
