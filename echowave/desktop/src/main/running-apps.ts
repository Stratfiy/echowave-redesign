/**
 * The apps open right now, for the settings window's "Add an app" list.
 * Never-touch apps (system security, password managers) are left out of
 * what can be picked by `canBeAllowed`, and the settings window says why.
 */

import { execFile } from 'node:child_process';

import { canBeAllowed } from '../computer-use/policy';

const run = (file: string, args: string[]) =>
    new Promise<string>((resolve) => {
        execFile(file, args, { timeout: 5000, windowsHide: true }, (err, stdout) =>
            resolve(err ? '' : String(stdout)),
        );
    });

export async function runningApps(platform: NodeJS.Platform): Promise<string[]> {
    let out = '';
    if (platform === 'darwin') {
        out = await run('osascript', [
            '-e',
            'tell application "System Events" to get name of every application process whose background only is false',
        ]);
        out = out.split(', ').join('\n');
    } else if (platform === 'win32') {
        out = await run('powershell.exe', [
            '-NoProfile',
            '-Command',
            'Get-Process | Where-Object { $_.MainWindowTitle } | ForEach-Object { if ($_.Description) { $_.Description } else { $_.ProcessName } } | Sort-Object -Unique',
        ]);
    }
    return [
        ...new Set(
            out
                .split(/\r?\n/)
                .map((s) => s.trim())
                .filter(Boolean),
        ),
    ]
        .filter((name) => canBeAllowed(name) && name !== 'Decibyl')
        .sort((a, b) => a.localeCompare(b));
}
