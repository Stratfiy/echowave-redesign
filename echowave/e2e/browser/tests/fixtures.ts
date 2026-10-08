import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * Shared pieces: signing in through the real form, an API handle for setup
 * and for reading back what the page should show, and a watch on the
 * browser console that fails the test on any error.
 */

export const API = `${(process.env.STAGING_URL || "http://127.0.0.1:8000").replace(/\/$/, "")}/api/v1`;
export const REQUIRE_MODEL = ["1", "true"].includes((process.env.E2E_REQUIRE_MODEL || "").toLowerCase());
export const FAILURE_REPLY = "I could not think that through";

/** A is the workspace owner; B a plain member A invited. */
export type Who = "A" | "B";

function credentials(who: Who = "A") {
    const email = process.env[`STAGING_EMAIL_${who}`];
    const password = process.env[`STAGING_PASSWORD_${who}`];
    if (!email || !password) throw new Error(`STAGING_EMAIL_${who}/STAGING_PASSWORD_${who} are not set`);
    return { email, password };
}

/** An API handle signed in as A or B, for setup and for reading back. */
export async function apiAs(playwright: { request: { newContext: (options?: object) => Promise<APIRequestContext> } }, who: Who) {
    const { email, password } = credentials(who);
    const anonymous = await playwright.request.newContext();
    const login = await anonymous.post(`${API}/auth/login`, { data: { email, password } });
    expect(login.status(), `API sign in as ${who}`).toBe(200);
    const { token } = await login.json();
    await anonymous.dispose();
    return playwright.request.newContext({ extraHTTPHeaders: { authorization: `Bearer ${token}` } });
}

/** A 503 that says the feature is not set up here is an honest state, not a fault. */
const NOT_CONFIGURED = /not (configured|set up)|cannot reach|are not set/i;

type Problems = { console: string[]; pageErrors: string[]; serverErrors: string[] };

type Fixtures = {
    problems: Problems;
    api: APIRequestContext;
    signedIn: Page;
};

export const test = base.extend<Fixtures>({
    problems: [
        async ({ page }, use, testInfo) => {
            const problems: Problems = { console: [], pageErrors: [], serverErrors: [] };
            page.on("pageerror", (error) => problems.pageErrors.push(String(error).slice(0, 500)));
            page.on("console", (message) => {
                if (message.type() !== "error") return;
                const text = message.text();
                // A failed request is logged by the browser as a console error.
                // Its status is judged below from the response itself: a 4xx is
                // an answer (a flag that is off, a refused permission); a 5xx is not.
                if (text.startsWith("Failed to load resource")) return;
                problems.console.push(text.slice(0, 500));
            });
            page.on("response", async (response) => {
                // Rate-limited: what the page shows is then not what it would
                // show a person, so the run proves nothing. Pace the test.
                if (response.status() === 429) {
                    problems.serverErrors.push(`HTTP 429 (rate limited) ${response.url()}`);
                    return;
                }
                if (response.status() < 500) return;
                let body = "";
                try {
                    body = (await response.text()).slice(0, 300);
                } catch {
                    /* the page moved on */
                }
                if (response.status() === 503 && NOT_CONFIGURED.test(body)) return;
                problems.serverErrors.push(`HTTP ${response.status()} ${response.url()} ${body}`);
            });
            await use(problems);
            const all = [
                ...problems.pageErrors.map((e) => `page error: ${e}`),
                ...problems.console.map((e) => `console error: ${e}`),
                ...problems.serverErrors.map((e) => `server error: ${e}`),
            ];
            if (all.length) {
                await testInfo.attach("browser-problems", { body: all.join("\n"), contentType: "text/plain" });
            }
            expect(all, "the browser console and network stayed clean").toEqual([]);
        },
        { auto: true },
    ],

    api: async ({ playwright }, use) => {
        const api = await apiAs(playwright, "A");
        await use(api);
        await api.dispose();
    },

    signedIn: async ({ page }, use) => {
        await signIn(page);
        await use(page);
    },
});

export { expect };

/** Sign in through the form, as a person does, and get past the one-time
 * prompts a fresh account meets (first-task setup, the workspace agreement). */
export async function signIn(page: Page, who: Who = "A") {
    const { email, password } = credentials(who);
    await page.goto("/auth/login");
    await page.getByTestId("login-email-input").fill(email);
    await page.getByTestId("login-next").click();
    await page.getByTestId("login-password-input").fill(password);
    await page.getByTestId("login-submit-btn").click();
    // Past the sign-in screens, including the hop that decides where to land,
    // before looking for the first-task screen.
    // An address not yet verified is offered the six-digit code first; the
    // suite's accounts take "Do this later", the way a person in a hurry would.
    await page.waitForURL((url) => url.pathname === "/auth/verify" || (!url.pathname.startsWith("/auth/") && url.pathname !== "/after-sign-in"), { timeout: 45_000 });
    if (new URL(page.url()).pathname === "/auth/verify") {
        await page.getByText("Do this later", { exact: true }).click();
        await page.waitForURL((url) => !url.pathname.startsWith("/auth/") && url.pathname !== "/after-sign-in", { timeout: 45_000 });
    }
    if (new URL(page.url()).pathname.startsWith("/welcome")) {
        await page.getByRole("button", { name: "Skip for now" }).click();
        await page.waitForURL((url) => !url.pathname.startsWith("/welcome"));
    }
    await dismissPrompts(page);
}

/** "Not now" on the agreement prompt: it records nothing and returns later. */
export async function dismissPrompts(page: Page) {
    const notNow = page.getByRole("button", { name: "Not now" });
    try {
        await notNow.waitFor({ state: "visible", timeout: 4_000 });
        await notNow.click();
    } catch {
        /* no prompt for this account */
    }
}

/** Elements that run past the left or right edge of the viewport, outside
 * any box that scrolls sideways on purpose (a row of chips, a table).
 *
 * Measured against the configured viewport, never ``window.innerWidth``: on
 * a phone the browser widens its layout viewport to fit content that is too
 * wide, so ``innerWidth`` grows with the very overflow being looked for. */
export async function offScreen(page: Page): Promise<string[]> {
    const viewport = page.viewportSize();
    if (!viewport) throw new Error("no viewport");
    return page.evaluate((width) => {
        const found: string[] = [];
        if (document.documentElement.scrollWidth > width + 1) {
            found.push(`the page scrolls sideways: ${document.documentElement.scrollWidth}px wide in ${width}px`);
        }
        const scrollsSideways = (el: Element | null): boolean => {
            for (let node = el?.parentElement; node && node !== document.body; node = node.parentElement) {
                const style = getComputedStyle(node);
                if (["auto", "scroll", "hidden", "clip"].includes(style.overflowX)) return true;
            }
            return false;
        };
        for (const el of Array.from(document.body.querySelectorAll("*"))) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = getComputedStyle(el);
            if (style.visibility === "hidden" || style.display === "none" || style.position === "fixed" && rect.right <= 0) continue;
            if (rect.right <= width + 1 && rect.left >= -1) continue;
            if (scrollsSideways(el)) continue;
            const label = (el.getAttribute("data-testid") || el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 60);
            found.push(`<${el.tagName.toLowerCase()}> "${label}" spans ${Math.round(rect.left)}..${Math.round(rect.right)} in ${width}px`);
            if (found.length >= 8) break;
        }
        return found;
    }, viewport.width);
}

export function marker(label: string) {
    return `e2e-${label}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}
