// Build installable packages:
//   node scripts/package.mjs mac|mac-zip|win|dir [--publish]
//
// `mac` builds the .dmg and .zip and needs a Mac (hdiutil, sips, codesign,
// notarytool). `mac-zip` builds only the .zip and also runs on Linux, which
// is how an unsigned macOS package is produced in a Linux container. `win`
// runs on Windows, or on Linux with Wine (32- and 64-bit). `dir` is an
// unpacked Linux build, for a smoke test only.
//
// Signs when the certificate variables are present, and says plainly when
// it is building unsigned. Never reads a secret from a file in the repo.
import { spawnSync } from 'node:child_process';

const target = process.argv[2];
const publish = process.argv.includes('--publish');
const PLATFORM = { mac: '--mac', 'mac-zip': '--mac', win: '--win', dir: '--linux' };
if (!PLATFORM[target]) {
    console.error('usage: node scripts/package.mjs mac|mac-zip|win|dir [--publish]');
    process.exit(2);
}
if (publish && !process.env.DECIBYL_UPDATE_URL) {
    console.error('Publishing needs DECIBYL_UPDATE_URL (where the update feed lives).');
    process.exit(2);
}

const env = { ...process.env };
env.DECIBYL_UPDATE_URL ||= 'https://downloads.decibyl.ai/desktop';
const args = ['electron-builder', PLATFORM[target]];
if (target === 'mac-zip') args.push('zip');
if (target === 'dir') args.push('--dir');
args.push('--publish', publish ? 'always' : 'never');

if (target === 'mac' || target === 'mac-zip') {
    const signed = Boolean(env.CSC_LINK || env.CSC_NAME);
    const notarise = Boolean(
        (env.APPLE_API_KEY && env.APPLE_API_KEY_ID && env.APPLE_API_ISSUER) ||
        (env.APPLE_ID && env.APPLE_APP_SPECIFIC_PASSWORD && env.APPLE_TEAM_ID),
    );
    if (!signed) {
        env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
        args.push('-c.mac.identity=null');
        console.warn(
            'No Developer ID certificate (CSC_LINK): building an UNSIGNED macOS package. Gatekeeper will block it on other Macs.',
        );
    }
    if (!signed || !notarise) {
        args.push('-c.mac.notarize=false');
        if (signed) {
            console.warn('No notarisation credentials: signed but NOT notarised.');
        }
    }
}
if (target === 'win') {
    const signed = Boolean(env.WIN_CSC_LINK || env.CSC_LINK || env.AZURE_TENANT_ID);
    if (!signed) {
        env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
        console.warn(
            'No Windows code-signing certificate (WIN_CSC_LINK): building an UNSIGNED installer. SmartScreen will warn.',
        );
    }
}

const result = spawnSync('npx', args, { stdio: 'inherit', env, shell: process.platform === 'win32' });
process.exit(result.status ?? 1);
