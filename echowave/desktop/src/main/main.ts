/**
 * Decibyl for Windows and macOS.
 *
 * One window that loads the Decibyl web app, plus the app's own small
 * windows: quick ask (global shortcut), the "Decibyl is working" bar, and
 * settings. A tray / menu-bar icon, native notifications bridged from the
 * web app, decibyl:// links, start at login (opt-in), auto-update.
 *
 * Security defaults on every window: context isolation, sandbox, no Node.
 * The web app gets `window.decibylDesktop` (src/preload/preload.ts) and
 * nothing else; main re-checks each call's origin (src/main/ipc.ts).
 */

import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
    BrowserWindow,
    Menu,
    Notification,
    Tray,
    app,
    desktopCapturer,
    dialog,
    globalShortcut,
    ipcMain,
    nativeImage,
    safeStorage,
    screen,
    session as electronSession,
    shell,
} from 'electron';

import {
    type Session,
    computerUseAvailable,
    httpApprovals,
    postReceipt,
} from '../computer-use/approvals-http';
import { DEFAULT_MODEL, anthropicModel } from '../computer-use/model';
import { nativeDriver } from '../computer-use/native-driver';
import type { AgentEvent } from '../computer-use/types';
import { buildChannels } from './channels';
import { ComputerSessions } from './computer';
import { SettingsStore, resolveWebUrl, rulesOf } from './config';
import { PROTOCOL, linkFromArgv, parseDeepLink } from './deeplink';
import { readPicked, readWatched, watchFolder } from './files';
import { createBridge } from './ipc';
import { runningApps } from './running-apps';
import { configureUpdates } from './updater';

const RENDERER = path.join(__dirname, '..', 'renderer');
const PRELOAD = path.join(__dirname, '..', 'preload', 'preload.js');
const ICON = path.join(
    __dirname,
    '..',
    '..',
    'build',
    process.platform === 'darwin' ? 'trayTemplate.png' : 'tray.png',
);
const STOP_SHORTCUT = 'CommandOrControl+Shift+.';

let mainWindow: BrowserWindow | null = null;
let quickWindow: BrowserWindow | null = null;
let barWindow: BrowserWindow | null = null;
let settingsWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let webSession: Session | null = null;
let features: Record<string, boolean> = {};
let stopWatching: (() => void) | null = null;
let pendingLink: string | null = linkFromArgv(process.argv);

if (!app.requestSingleInstanceLock()) {
    app.quit();
}

const store = new SettingsStore(path.join(app.getPath('userData'), 'settings.json'));
const keyFile = path.join(app.getPath('userData'), 'model-key.bin');
const receiptsDir = path.join(app.getPath('userData'), 'receipts');

const webUrl = () => resolveWebUrl(store.get(), process.argv, process.env);
const webOrigin = () => new URL(webUrl()).origin;

// --- windows ----------------------------------------------------------------

const secure = {
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    preload: PRELOAD,
    spellcheck: true,
};

function showMain(pathInApp?: string): void {
    if (!mainWindow || mainWindow.isDestroyed()) {
        mainWindow = new BrowserWindow({
            width: 1280,
            height: 860,
            minWidth: 380,
            minHeight: 560,
            show: false,
            title: 'Decibyl',
            webPreferences: secure,
        });
        mainWindow.once('ready-to-show', () => mainWindow?.show());
        // Links out of the app open in the browser; only https.
        mainWindow.webContents.setWindowOpenHandler(({ url }) => {
            if (url.startsWith('https://')) void shell.openExternal(url);
            return { action: 'deny' };
        });
        mainWindow.on('close', (event) => {
            // Closing the window keeps Decibyl in the tray, the way chat apps do.
            if (!quitting) {
                event.preventDefault();
                mainWindow?.hide();
            }
        });
        void mainWindow.loadURL(`${webUrl()}${pathInApp ?? '/'}`);
    } else if (pathInApp) {
        void mainWindow.loadURL(`${webUrl()}${pathInApp}`);
    }
    mainWindow.show();
    mainWindow.focus();
}

function localWindow(
    file: string,
    options: Electron.BrowserWindowConstructorOptions,
): BrowserWindow {
    const win = new BrowserWindow({ ...options, webPreferences: secure });
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    void win.loadFile(path.join(RENDERER, file));
    return win;
}

function toggleQuick(prefill = ''): void {
    if (quickWindow && !quickWindow.isDestroyed() && quickWindow.isVisible() && !prefill) {
        quickWindow.hide();
        return;
    }
    if (!quickWindow || quickWindow.isDestroyed()) {
        quickWindow = localWindow('quick.html', {
            width: 620,
            height: 168,
            frame: false,
            resizable: false,
            alwaysOnTop: true,
            skipTaskbar: true,
            show: false,
            transparent: false,
        });
        quickWindow.on('blur', () => quickWindow?.hide());
    }
    const { workArea } = screen.getPrimaryDisplay();
    quickWindow.setPosition(
        Math.round(workArea.x + (workArea.width - 620) / 2),
        Math.round(workArea.y + workArea.height * 0.22),
    );
    quickWindow.show();
    quickWindow.focus();
    if (prefill) quickWindow.webContents.send('quick:prefill', prefill);
}

function showSettings(): void {
    if (settingsWindow && !settingsWindow.isDestroyed()) {
        settingsWindow.show();
        settingsWindow.focus();
        return;
    }
    settingsWindow = localWindow('settings.html', {
        width: 640,
        height: 760,
        title: 'Decibyl settings',
        show: true,
    });
}

/** The always-visible bar. Kept out of screenshots by content protection. */
const bar = {
    show(task: string) {
        if (!barWindow || barWindow.isDestroyed()) {
            const { workArea } = screen.getPrimaryDisplay();
            barWindow = localWindow('bar.html', {
                width: 460,
                height: 64,
                x: Math.round(workArea.x + (workArea.width - 460) / 2),
                y: workArea.y + 8,
                frame: false,
                resizable: false,
                movable: true,
                skipTaskbar: true,
                focusable: false,
                alwaysOnTop: true,
                show: false,
            });
            barWindow.setAlwaysOnTop(true, 'screen-saver');
            barWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
            barWindow.setContentProtection(true);
            barWindow.webContents.once('did-finish-load', () =>
                barWindow?.webContents.send('bar:update', { type: 'started', task }),
            );
        }
        barWindow.showInactive();
        globalShortcut.register(STOP_SHORTCUT, () => computer.stop());
        refreshTray();
    },
    update(event: AgentEvent) {
        barWindow?.webContents.send('bar:update', event);
        mainWindow?.webContents.send('computer:event', event);
    },
    hide() {
        globalShortcut.unregister(STOP_SHORTCUT);
        barWindow?.destroy();
        barWindow = null;
        refreshTray();
    },
};

// --- notifications, files ------------------------------------------------------

function notify(title: string, body: string, pathInApp = '/'): void {
    if (!Notification.isSupported()) return;
    const n = new Notification({ title, body, silent: false });
    n.on('click', () => showMain(pathInApp));
    n.show();
}

function applyWatchedFolder(): void {
    stopWatching?.();
    stopWatching = null;
    const folder = store.get().watchedFolder;
    if (!folder || !features.desktop_app) return;
    try {
        stopWatching = watchFolder(folder, (file) => {
            const n = new Notification({
                title: `New in ${path.basename(folder)}`,
                body: `${file.name} — click to add it to a chat.`,
            });
            n.on('click', () => {
                showMain();
                try {
                    mainWindow?.webContents.send('files:incoming', {
                        files: [readWatched(folder, file.name)],
                        skipped: [],
                    });
                } catch (err) {
                    notify('Could not read that file', (err as Error).message);
                }
            });
            n.show();
        });
    } catch (err) {
        notify('Decibyl cannot watch that folder', (err as Error).message);
    }
}

// --- the model key ------------------------------------------------------------

const apiKey = {
    set(key: string) {
        if (!safeStorage.isEncryptionAvailable())
            throw new Error('This computer cannot store the key securely.');
        fs.mkdirSync(path.dirname(keyFile), { recursive: true });
        fs.writeFileSync(keyFile, safeStorage.encryptString(key), { mode: 0o600 });
    },
    clear() {
        fs.rmSync(keyFile, { force: true });
    },
    present() {
        return fs.existsSync(keyFile);
    },
    read(): string | null {
        try {
            return safeStorage.decryptString(fs.readFileSync(keyFile));
        } catch {
            return null;
        }
    },
};

// --- work on my computer -------------------------------------------------------

const computer = new ComputerSessions({
    driver: () => nativeDriver({ desktopCapturer, screen } as never),
    model: () => {
        const key = apiKey.read() ?? process.env.DECIBYL_MODEL_KEY ?? null;
        if (!key) return null;
        const s = store.get().computerUse;
        return anthropicModel({
            ...DEFAULT_MODEL,
            apiKey: key,
            model: s.model ?? DEFAULT_MODEL.model,
            baseURL: s.baseURL ?? undefined,
        });
    },
    approvals: (sessionId) =>
        httpApprovals({ session: () => webSession, sessionId, device: os.hostname() }),
    available: async () =>
        Boolean(features.desktop_computer_use) && (await computerUseAvailable(webSession)),
    signedIn: () => webSession !== null,
    config: () => {
        const s = store.get();
        return {
            rules: rulesOf(s),
            allowedApps: s.computerUse.allowedApps,
            limits: s.computerUse.limits,
            rates: DEFAULT_MODEL.rates,
        };
    },
    bar,
    saveReceipt: (receipt) => {
        fs.mkdirSync(receiptsDir, { recursive: true });
        fs.writeFileSync(
            path.join(receiptsDir, `${receipt.startedAt.replace(/[:.]/g, '-')}-${receipt.id}.json`),
            JSON.stringify(receipt, null, 2),
        );
    },
    postReceipt: (text) => postReceipt(webSession, text),
    notify: (title, body) => notify(title, body),
    newId: () => randomUUID(),
});

async function loadFeatures(): Promise<void> {
    features = {};
    if (!webSession) return;
    try {
        const response = await fetch(`${webSession.apiBase}/api/v1/features`, {
            headers: { authorization: `Bearer ${webSession.token}` },
        });
        if (response.ok) features = (await response.json()) as Record<string, boolean>;
    } catch {
        features = {};
    }
    applyWatchedFolder();
    refreshTray();
}

// --- tray -----------------------------------------------------------------------

function refreshTray(): void {
    if (!tray) return;
    const items: Electron.MenuItemConstructorOptions[] = [
        { label: 'Open Decibyl', click: () => showMain() },
        { label: 'Ask Decibyl…', accelerator: store.get().shortcut, click: () => toggleQuick() },
    ];
    if (computer.active)
        items.push({
            label: 'Stop working on my computer',
            accelerator: STOP_SHORTCUT,
            click: () => computer.stop(),
        });
    items.push(
        { type: 'separator' },
        { label: 'Settings…', click: showSettings },
        { label: 'Check for updates', click: () => updates?.check(true) },
        { type: 'separator' },
        { label: 'Quit Decibyl', click: () => app.quit() },
    );
    tray.setContextMenu(Menu.buildFromTemplate(items));
    tray.setToolTip(computer.active ? 'Decibyl is working on your computer' : 'Decibyl');
}

// --- links ------------------------------------------------------------------------

function openLink(raw: string): void {
    const link = parseDeepLink(raw);
    if (!link) return;
    if (link.kind === 'ask') toggleQuick(link.text || ' ');
    else showMain(link.path);
}

app.setAsDefaultProtocolClient(PROTOCOL);
app.on('open-url', (event, url) => {
    event.preventDefault();
    if (app.isReady()) openLink(url);
    else pendingLink = url;
});
app.on('second-instance', (_event, argv) => {
    const link = linkFromArgv(argv);
    if (link) openLink(link);
    else showMain();
});

// --- start ----------------------------------------------------------------------------

let quitting = false;
let updates: ReturnType<typeof configureUpdates> | null = null;

function applyLoginItem(): void {
    if (process.platform === 'darwin' || process.platform === 'win32') {
        app.setLoginItemSettings({ openAtLogin: store.get().startAtLogin, args: ['--hidden'] });
    }
}

function registerShortcut(): void {
    globalShortcut.unregisterAll();
    const ok = globalShortcut.register(store.get().shortcut, () => toggleQuick());
    if (!ok)
        notify(
            'Shortcut not available',
            `${store.get().shortcut} is used by another app. Pick another in Decibyl settings.`,
        );
    if (computer.active) globalShortcut.register(STOP_SHORTCUT, () => computer.stop());
}

const bridge = createBridge({
    webOrigin,
    localRoot: RENDERER,
    channels: buildChannels({
        version: app.getVersion(),
        platform: process.platform,
        environment: () => store.get().environment,
        notify: (title, body, p) => notify(title, body, p),
        setSession: (s) => {
            webSession = s;
            void loadFeatures();
        },
        setThread: (threadId) => {
            if (webSession) webSession = { ...webSession, threadId };
        },
        pickFiles: async (folders) => {
            const options: Electron.OpenDialogOptions = {
                properties: folders ? ['openDirectory'] : ['openFile', 'multiSelections'],
            };
            const result = mainWindow
                ? await dialog.showOpenDialog(mainWindow, options)
                : await dialog.showOpenDialog(options);
            return result.canceled ? { files: [], skipped: [] } : readPicked(result.filePaths);
        },
        chooseWatchedFolder: async () => {
            const result = await dialog.showOpenDialog({
                properties: ['openDirectory', 'createDirectory'],
                title: 'Watch a folder for new files',
            });
            if (result.canceled || !result.filePaths[0]) return store.get().watchedFolder;
            store.setWatchedFolder(result.filePaths[0]);
            applyWatchedFolder();
            return result.filePaths[0];
        },
        clearWatchedFolder: () => {
            store.setWatchedFolder(null);
            applyWatchedFolder();
        },
        settings: {
            get: () => store.get(),
            update: (change) => {
                const next = store.update(change);
                applyLoginItem();
                registerShortcut();
                updates?.setChannel(next.updateChannel);
                settingsWindow?.webContents.send('settings:changed', next);
                return next;
            },
        },
        apiKey,
        runningApps: () => runningApps(process.platform),
        computer,
        askInMainWindow: (text) => {
            quickWindow?.hide();
            showMain(`/?say=${encodeURIComponent(text)}`);
        },
        closeQuick: () => quickWindow?.hide(),
        canUseFiles: () => Boolean(features.desktop_app),
    }),
});

ipcMain.handle('decibyl', (event, channel: unknown, payload: unknown) =>
    bridge.handle(String(channel), event.senderFrame?.url ?? event.sender.getURL(), payload),
);

app.whenReady().then(() => {
    // Ask before anything sensitive; allow what the web app needs to talk.
    electronSession.defaultSession.setPermissionRequestHandler(
        (wc, permission, callback, details) => {
            const fromWeb = (() => {
                try {
                    return new URL(details.requestingUrl).origin === webOrigin();
                } catch {
                    return false;
                }
            })();
            callback(
                fromWeb &&
                    ['notifications', 'media', 'clipboard-sanitized-write', 'fullscreen'].includes(
                        permission,
                    ),
            );
            void wc;
        },
    );

    const image = nativeImage.createFromPath(ICON);
    tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
    tray.on('click', () => showMain());
    refreshTray();

    applyLoginItem();
    registerShortcut();
    updates = configureUpdates({ channel: store.get().updateChannel, notify });
    if (!process.argv.includes('--hidden')) showMain();
    if (pendingLink) {
        openLink(pendingLink);
        pendingLink = null;
    }
});

app.on('before-quit', () => {
    quitting = true;
    computer.stop();
});
app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => {
    // Stay in the tray / menu bar.
});
app.on('activate', () => showMain());
