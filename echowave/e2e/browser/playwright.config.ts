import { defineConfig } from "@playwright/test";

/**
 * Browser checks against a running Decibyl, at a phone's width and a
 * laptop's. One worker, always: the CI box that runs staging has 8 GB and
 * also runs the api, the ui and their databases.
 *
 *   STAGING_UI_URL   the web app (default http://127.0.0.1:3010)
 *   STAGING_URL      the API, for setup and for reading what the page shows
 *   STAGING_EMAIL_A / STAGING_PASSWORD_A   the account that signs in
 */
const uiUrl = (process.env.STAGING_UI_URL || "http://127.0.0.1:3010").replace(/\/$/, "");
const outputDir = process.env.E2E_BROWSER_OUTPUT || "test-results";

export default defineConfig({
    testDir: "./tests",
    outputDir: `${outputDir}/artifacts`,
    workers: 1,
    fullyParallel: false,
    retries: 0,
    timeout: 180_000,
    expect: { timeout: 20_000 },
    reporter: [
        ["list"],
        ["junit", { outputFile: `${outputDir}/junit.xml` }],
        ["html", { outputFolder: `${outputDir}/html`, open: "never" }],
    ],
    use: {
        baseURL: uiUrl,
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
        actionTimeout: 20_000,
        navigationTimeout: 45_000,
    },
    projects: [
        {
            name: "phone-390",
            use: { browserName: "chromium", viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
        },
        {
            name: "desktop-1280",
            use: { browserName: "chromium", viewport: { width: 1280, height: 800 } },
        },
    ],
});
