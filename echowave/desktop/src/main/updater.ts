/**
 * Auto-update through electron-updater.
 *
 * Where updates come from is set at build time in electron-builder.yml
 * (`publish`), so a release is whatever the release job uploaded there. The
 * channel is `latest` or `beta`, chosen in settings. Nothing happens in a
 * development run, or when DECIBYL_DISABLE_UPDATES=1 (managed computers that
 * update through their own tooling).
 */

import { app } from 'electron';
import { autoUpdater } from 'electron-updater';

export function configureUpdates(opts: {
    channel: 'latest' | 'beta';
    notify: (title: string, body: string) => void;
}) {
    const enabled = app.isPackaged && process.env.DECIBYL_DISABLE_UPDATES !== '1';
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.channel = opts.channel;
    autoUpdater.allowPrerelease = opts.channel === 'beta';
    autoUpdater.on('update-downloaded', (info) => {
        opts.notify(
            'Decibyl update ready',
            `Version ${info.version} installs when you quit Decibyl.`,
        );
    });
    autoUpdater.on('error', () => {
        // An update that cannot be fetched now is fetched next time; the app keeps working.
    });

    const check = (manual = false) => {
        if (!enabled) {
            if (manual) opts.notify('Updates', 'Updates are off in this build.');
            return;
        }
        void autoUpdater.checkForUpdates().catch(() => undefined);
    };
    if (enabled) {
        setTimeout(() => check(), 10_000);
        setInterval(() => check(), 6 * 60 * 60 * 1000);
    }
    return {
        check,
        setChannel(channel: 'latest' | 'beta') {
            autoUpdater.channel = channel;
            autoUpdater.allowPrerelease = channel === 'beta';
        },
    };
}
