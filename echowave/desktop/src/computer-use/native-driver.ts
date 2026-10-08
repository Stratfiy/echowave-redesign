/**
 * The real screen, keyboard and mouse.
 *
 * - Screenshots: Electron's `desktopCapturer`, asked for a thumbnail already
 *   at the size the model gets, so a full-resolution capture of the person's
 *   screen is never held for longer than a zoom needs it. Never written to
 *   disk. The "Decibyl is working" bar sets content protection, so it is not
 *   in them.
 * - Mouse and keyboard: `@nut-tree-fork/nut-js` (prebuilt per platform, an
 *   optional dependency), loaded on first use. If it did not install, every
 *   input call fails with a line that says so instead of pretending to work.
 * - Which app is in front, and whether the focused field is a password
 *   field: the OS accessibility APIs, via `osascript` on macOS and UI
 *   Automation through PowerShell on Windows. When the OS will not say, the
 *   answer is null, and the loop treats a null as a password field.
 *
 * All coordinates are logical screen points (what `screen.getPrimaryDisplay()
 * .size` reports), the same space nut-js moves the mouse in.
 */

import { execFile } from 'node:child_process';

import type {
    ComputerDriver,
    ElementInfo,
    FocusedElement,
    FrontApp,
    MouseButton,
    Point,
    Region,
    Screenshot,
} from './types';

type Run = (file: string, args: string[]) => Promise<string>;

const run: Run = (file, args) =>
    new Promise((resolve, reject) => {
        execFile(file, args, { timeout: 4000, windowsHide: true }, (err, stdout) =>
            err ? reject(err) : resolve(String(stdout)),
        );
    });

const MAC_FRONT = `tell application "System Events"
  set p to first application process whose frontmost is true
  set t to ""
  try
    set t to name of front window of p
  end try
  return (name of p) & "\n" & (bundle identifier of p) & "\n" & t
end tell`;

const MAC_FOCUSED = `tell application "System Events"
  set p to first application process whose frontmost is true
  set e to value of attribute "AXFocusedUIElement" of p
  set r to ""
  set s to ""
  try
    set r to value of attribute "AXRole" of e
  end try
  try
    set s to value of attribute "AXSubrole" of e
  end try
  return r & "\n" & s
end tell`;

const WIN_FRONT = `Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class W { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int p);
[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n); }
"@
$h=[W]::GetForegroundWindow(); $p=0; [void][W]::GetWindowThreadProcessId($h,[ref]$p)
$sb=New-Object System.Text.StringBuilder 512; [void][W]::GetWindowText($h,$sb,512)
$proc=Get-Process -Id $p
Write-Output ($proc.MainModule.FileVersionInfo.FileDescription); Write-Output ($proc.ProcessName); Write-Output $sb.ToString()`;

const WIN_FOCUSED = `Add-Type -AssemblyName UIAutomationClient
$e=[System.Windows.Automation.AutomationElement]::FocusedElement
Write-Output $e.Current.IsPassword; Write-Output $e.Current.ControlType.ProgrammaticName; Write-Output $e.Current.Name`;

const winAt = (
    x: number,
    y: number,
) => `Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName WindowsBase
$e=[System.Windows.Automation.AutomationElement]::FromPoint((New-Object System.Windows.Point(${x},${y})))
Write-Output $e.Current.ControlType.ProgrammaticName; Write-Output $e.Current.Name`;

interface Electronish {
    desktopCapturer: {
        getSources(o: {
            types: string[];
            thumbnailSize: { width: number; height: number };
        }): Promise<Array<{ display_id: string; thumbnail: NativeImageish }>>;
    };
    screen: {
        getPrimaryDisplay(): {
            id: number;
            size: { width: number; height: number };
            scaleFactor: number;
        };
    };
}

interface NativeImageish {
    getSize(): { width: number; height: number };
    crop(r: { x: number; y: number; width: number; height: number }): NativeImageish;
    resize(o: { width: number; height: number }): NativeImageish;
    toPNG(): Buffer;
}

export function nativeDriver(
    electron: Electronish,
    platform: NodeJS.Platform = process.platform,
    exec: Run = run,
): ComputerDriver {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let nut: any = null;
    const input = async () => {
        if (nut) return nut;
        try {
            // Optional and native: see the module comment.
            nut = await import('@nut-tree-fork/nut-js' as string);
            nut.keyboard.config.autoDelayMs = 12;
            nut.mouse.config.mouseSpeed = 3000;
            return nut;
        } catch {
            throw new Error(
                'Mouse and keyboard control is not installed in this build of Decibyl.',
            );
        }
    };

    const display = () => electron.screen.getPrimaryDisplay();

    const capture = async (width: number, height: number): Promise<NativeImageish> => {
        const sources = await electron.desktopCapturer.getSources({
            types: ['screen'],
            thumbnailSize: { width, height },
        });
        const primary = sources.find((s) => s.display_id === String(display().id)) ?? sources[0];
        if (!primary)
            throw new Error(
                'The screen could not be captured. Screen recording permission may be off.',
            );
        return primary.thumbnail;
    };

    const fit = (w: number, h: number, max: number) => {
        const s = Math.min(1, max / Math.max(w, h));
        return { width: Math.round(w * s), height: Math.round(h * s) };
    };

    const toShot = (img: NativeImageish): Screenshot => {
        const { width, height } = img.getSize();
        return { data: img.toPNG().toString('base64'), mediaType: 'image/png', width, height };
    };

    const keyList = (n: { Key: Record<string, unknown> }, combo: string): unknown[] =>
        combo
            .split('+')
            .map((k) => k.trim())
            .filter(Boolean)
            .map((k) => {
                const name =
                    KEY_ALIASES[k.toLowerCase()] ??
                    (k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1));
                const key = n.Key[name];
                if (key === undefined) throw new Error(`Unknown key ${k}.`);
                return key;
            });

    const withModifiers = async (mods: string[], fn: () => Promise<void>) => {
        const n = await input();
        const keys = mods.length ? keyList(n, mods.join('+')) : [];
        if (keys.length) await n.keyboard.pressKey(...keys);
        try {
            await fn();
        } finally {
            if (keys.length) await n.keyboard.releaseKey(...keys);
        }
    };

    const moveTo = async (p: Point) => {
        const n = await input();
        await n.mouse.setPosition(new n.Point(p[0], p[1]));
    };

    return {
        async screenSize() {
            return display().size;
        },
        async screenshot(maxLongEdge) {
            const { width, height } = display().size;
            return toShot(
                await capture(
                    fit(width, height, maxLongEdge).width,
                    fit(width, height, maxLongEdge).height,
                ),
            );
        },
        async zoom(region: Region, maxLongEdge) {
            const d = display();
            const full = await capture(
                Math.round(d.size.width * d.scaleFactor),
                Math.round(d.size.height * d.scaleFactor),
            );
            const k = full.getSize().width / d.size.width;
            const [x0, y0, x1, y1] = region;
            const crop = full.crop({
                x: Math.round(x0 * k),
                y: Math.round(y0 * k),
                width: Math.max(1, Math.round((x1 - x0) * k)),
                height: Math.max(1, Math.round((y1 - y0) * k)),
            });
            const size = crop.getSize();
            return toShot(crop.resize(fit(size.width, size.height, maxLongEdge)));
        },
        async frontApp(): Promise<FrontApp | null> {
            try {
                if (platform === 'darwin') {
                    const [name, id, title] = (await exec('osascript', ['-e', MAC_FRONT]))
                        .split('\n')
                        .map((s) => s.trim());
                    return name ? { name, id, windowTitle: title } : null;
                }
                if (platform === 'win32') {
                    const [desc, proc, title] = (
                        await exec('powershell.exe', ['-NoProfile', '-Command', WIN_FRONT])
                    )
                        .split(/\r?\n/)
                        .map((s) => s.trim());
                    return proc ? { name: desc || proc, id: proc, windowTitle: title } : null;
                }
            } catch {
                return null;
            }
            return null;
        },
        async focusedElement(): Promise<FocusedElement> {
            try {
                if (platform === 'darwin') {
                    const [role, subrole] = (await exec('osascript', ['-e', MAC_FOCUSED]))
                        .split('\n')
                        .map((s) => s.trim());
                    if (!role) return { secure: null };
                    return { secure: subrole === 'AXSecureTextField', role };
                }
                if (platform === 'win32') {
                    const [isPassword, role, label] = (
                        await exec('powershell.exe', ['-NoProfile', '-Command', WIN_FOCUSED])
                    )
                        .split(/\r?\n/)
                        .map((s) => s.trim());
                    if (isPassword !== 'True' && isPassword !== 'False') return { secure: null };
                    return {
                        secure: isPassword === 'True',
                        role: role?.replace('ControlType.', ''),
                        label,
                    };
                }
            } catch {
                return { secure: null };
            }
            return { secure: null };
        },
        async elementAt(p: Point): Promise<ElementInfo | null> {
            if (platform !== 'win32') return null; // macOS: AX hit-testing needs a native helper; the model declares instead
            try {
                const [role, label] = (
                    await exec('powershell.exe', ['-NoProfile', '-Command', winAt(p[0], p[1])])
                )
                    .split(/\r?\n/)
                    .map((s) => s.trim());
                return { role: role?.replace('ControlType.', ''), label };
            } catch {
                return null;
            }
        },
        async activateApp(name: string) {
            if (platform === 'darwin') {
                await exec('osascript', [
                    '-e',
                    `tell application ${JSON.stringify(name)} to activate`,
                ]);
                return;
            }
            if (platform === 'win32') {
                const safe = name.replace(/'/g, "''");
                await exec('powershell.exe', [
                    '-NoProfile',
                    '-Command',
                    `(New-Object -ComObject WScript.Shell).AppActivate('${safe}') | Out-Null`,
                ]);
                return;
            }
            throw new Error('Switching apps is not supported on this system.');
        },
        async click(button: MouseButton, point, count, modifiers) {
            const n = await input();
            if (point) await moveTo(point);
            const b = { left: n.Button.LEFT, right: n.Button.RIGHT, middle: n.Button.MIDDLE }[
                button
            ];
            await withModifiers(modifiers, async () => {
                for (let i = 0; i < count; i++) await n.mouse.click(b);
            });
        },
        async drag(from, to, modifiers) {
            const n = await input();
            await moveTo(from);
            await withModifiers(modifiers, async () => {
                await n.mouse.pressButton(n.Button.LEFT);
                await moveTo(to);
                await n.mouse.releaseButton(n.Button.LEFT);
            });
        },
        async move(p) {
            await moveTo(p);
        },
        async mouseDown() {
            const n = await input();
            await n.mouse.pressButton(n.Button.LEFT);
        },
        async mouseUp() {
            const n = await input();
            await n.mouse.releaseButton(n.Button.LEFT);
        },
        async cursor() {
            const n = await input();
            const p = await n.mouse.getPosition();
            return [p.x, p.y];
        },
        async scroll(direction, amount, point, modifiers) {
            const n = await input();
            if (point) await moveTo(point);
            const fn = {
                up: 'scrollUp',
                down: 'scrollDown',
                left: 'scrollLeft',
                right: 'scrollRight',
            }[direction];
            await withModifiers(modifiers, async () => {
                await n.mouse[fn](amount);
            });
        },
        async typeText(text) {
            const n = await input();
            await n.keyboard.type(text);
        },
        async pressKeys(combo, repeat) {
            const n = await input();
            const keys = keyList(n, combo);
            for (let i = 0; i < repeat; i++) {
                await n.keyboard.pressKey(...keys);
                await n.keyboard.releaseKey(...keys);
            }
        },
        async holdKeys(combo, seconds) {
            const n = await input();
            const keys = keyList(n, combo);
            await n.keyboard.pressKey(...keys);
            await new Promise((r) => setTimeout(r, seconds * 1000));
            await n.keyboard.releaseKey(...keys);
        },
        async wait(seconds) {
            await new Promise((r) => setTimeout(r, seconds * 1000));
        },
    };
}

/** xdotool-style names the model uses -> nut-js Key names. */
const KEY_ALIASES: Record<string, string> = {
    return: 'Enter',
    enter: 'Enter',
    kp_enter: 'Enter',
    esc: 'Escape',
    escape: 'Escape',
    tab: 'Tab',
    space: 'Space',
    backspace: 'Backspace',
    back_space: 'Backspace',
    delete: 'Delete',
    del: 'Delete',
    up: 'Up',
    down: 'Down',
    left: 'Left',
    right: 'Right',
    home: 'Home',
    end: 'End',
    page_up: 'PageUp',
    pageup: 'PageUp',
    page_down: 'PageDown',
    pagedown: 'PageDown',
    ctrl: 'LeftControl',
    control: 'LeftControl',
    shift: 'LeftShift',
    alt: 'LeftAlt',
    option: 'LeftAlt',
    cmd: 'LeftCmd',
    command: 'LeftCmd',
    super: process.platform === 'darwin' ? 'LeftCmd' : 'LeftWin',
    meta: process.platform === 'darwin' ? 'LeftCmd' : 'LeftWin',
};
