// Screenshots of one built site, for Studio's design review.
//
// Runs inside the screenshot box (see server.py: POST /screenshots), which has
// no network. The site is already unpacked in /work/site and served on
// 127.0.0.1:4173 by the box's script. This drives Chromium over the DevTools
// protocol with Node's own WebSocket -- no packages -- because the browser's
// --screenshot flag cannot emulate a phone: Linux will not make a window
// narrower than about 500px, so a "390px" shot is really a crop of a wider
// layout.
//
// Prints one JSON line: {shots: [{name, width, height, png}], errors, overflow}.

import { spawn } from "node:child_process";

const CHROME = process.env.CHROME || "/ms-playwright/chromium-1194/chrome-linux/chrome";
const URL_ = "http://127.0.0.1:4173/";
const PORT = 9333;
const MAX_HEIGHT = 2400;
const VIEWS = [
    { name: "desktop", width: 1280, height: 800, mobile: false },
    { name: "mobile", width: 390, height: 844, mobile: true },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const chrome = spawn(
    CHROME,
    [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        `--remote-debugging-port=${PORT}`,
        "--user-data-dir=/tmp/chrome",
        "about:blank",
    ],
    { stdio: "ignore" },
);

async function target() {
    for (let i = 0; i < 50; i++) {
        try {
            const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
            const page = list.find((t) => t.type === "page");
            if (page) return page.webSocketDebuggerUrl;
        } catch {
            // Not listening yet.
        }
        await sleep(100);
    }
    throw new Error("Chromium did not start");
}

async function main() {
    const ws = new WebSocket(await target());
    await new Promise((resolve, reject) => {
        ws.onopen = resolve;
        ws.onerror = reject;
    });
    let id = 0;
    const pending = new Map();
    const errors = [];
    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.id && pending.has(message.id)) {
            const { resolve, reject } = pending.get(message.id);
            pending.delete(message.id);
            if (message.error) reject(new Error(message.error.message));
            else resolve(message.result);
        } else if (message.method === "Runtime.exceptionThrown") {
            const d = message.params.exceptionDetails;
            errors.push((d.exception && d.exception.description) || d.text);
        } else if (
            message.method === "Runtime.consoleAPICalled" &&
            message.params.type === "error"
        ) {
            errors.push(message.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
        } else if (message.method === "Log.entryAdded" && message.params.entry.level === "error") {
            // Failed loads: a missing asset or a blocked network font.
            const url = message.params.entry.url || "";
            // A missing favicon is the browser's guess, not the page's error.
            if (!url.endsWith("/favicon.ico")) errors.push(`${message.params.entry.text} ${url}`.trim());
        }
    };
    const send = (method, params = {}) =>
        new Promise((resolve, reject) => {
            const n = ++id;
            pending.set(n, { resolve, reject });
            ws.send(JSON.stringify({ id: n, method, params }));
        });

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Log.enable");
    // Ask for reduced motion: scroll-triggered entrances (motion's
    // whileInView) never fire in a screenshot that does not scroll, and the
    // review would see blank sections a visitor never sees.
    await send("Emulation.setEmulatedMedia", {
        features: [{ name: "prefers-reduced-motion", value: "reduce" }],
    });

    const shots = [];
    const overflow = {};
    for (const view of VIEWS) {
        await send("Emulation.setDeviceMetricsOverride", {
            width: view.width,
            height: view.height,
            deviceScaleFactor: 1,
            mobile: view.mobile,
        });
        await send("Page.navigate", { url: URL_ });
        await sleep(2500);
        const metrics = await send("Runtime.evaluate", {
            expression:
                "JSON.stringify({h: document.documentElement.scrollHeight, w: document.documentElement.scrollWidth, iw: innerWidth, text: document.body.innerText.trim().length})",
            returnByValue: true,
        });
        const m = JSON.parse(metrics.result.value);
        overflow[view.name] = m.w > m.iw ? m.w - m.iw : 0;
        if (view.name === "desktop" && m.text === 0) errors.push("The page rendered no text at all.");
        const height = Math.min(Math.max(m.h, view.height), MAX_HEIGHT);
        const shot = await send("Page.captureScreenshot", {
            format: "png",
            captureBeyondViewport: true,
            clip: { x: 0, y: 0, width: view.width, height, scale: 1 },
        });
        shots.push({ name: view.name, width: view.width, height, png: shot.data });
    }
    ws.close();
    return { shots, errors: [...new Set(errors)].slice(0, 20), overflow };
}

// Exit only once the line is written: stdout is a pipe, and a process that
// exits mid-write leaves the reader 64 KB of a JSON line and no end.
main()
    .catch((error) => ({ shots: [], errors: [String(error)], overflow: {} }))
    .then((result) => {
        chrome.kill("SIGKILL");
        process.stdout.write(JSON.stringify(result) + "\n", () => process.exit(0));
    });
