import { describe, expect, it } from 'vitest';

import {
    appKey,
    canBeAllowed,
    classifyAction,
    isAllowed,
    isNeverTouch,
    keyboardRefusal,
    kindOfLabel,
} from '../src/computer-use/policy';

describe('app list', () => {
    it('normalises names', () => {
        expect(appKey('  Microsoft Excel.exe ')).toBe('microsoft excel');
        expect(appKey('Mail.app')).toBe('mail');
    });

    it('denies by default and allows only listed apps', () => {
        const rules = { mail: { allowed: true }, slack: { allowed: false } };
        expect(isAllowed({ name: 'Mail' }, rules)).toBe(true);
        expect(isAllowed({ name: 'Slack' }, rules)).toBe(false);
        expect(isAllowed({ name: 'Terminal' }, rules)).toBe(false);
        expect(isAllowed(null, rules)).toBe(false);
    });

    it('never touches system security windows, password managers or Decibyl itself', () => {
        for (const app of [
            { name: 'SecurityAgent' },
            { name: 'coreautha' },
            { name: 'Keychain Access' },
            { name: 'System Settings' },
            { name: 'consent.exe' },
            { name: 'Windows Security' },
            { name: 'Credential Manager UI Host', id: 'CredentialUIBroker' },
            { name: '1Password' },
            { name: 'Bitwarden' },
            { name: 'KeePassXC' },
            { name: 'Decibyl' },
            { name: 'Some app', id: 'com.1password.1password' },
            { name: 'Google Chrome', windowTitle: 'User Account Control' },
            {
                name: 'Installer',
                windowTitle:
                    'Installer is trying to install new software. Enter your password to allow this.',
            },
        ]) {
            expect(isNeverTouch(app), JSON.stringify(app)).toBe(true);
            expect(isAllowed(app, { [appKey(app.name)]: { allowed: true } })).toBe(false);
        }
        expect(canBeAllowed('1Password')).toBe(false);
        expect(isNeverTouch({ name: 'Mail', windowTitle: 'Inbox' })).toBe(false);
    });
});

describe('what counts as consequential', () => {
    it('reads labels', () => {
        expect(kindOfLabel('Send')).toBe('send');
        expect(kindOfLabel('Pay ₹4,800')).toBe('pay');
        expect(kindOfLabel('Place order')).toBe('pay');
        expect(kindOfLabel('Move to Bin')).toBe('delete');
        expect(kindOfLabel('Submit application')).toBe('submit');
        expect(kindOfLabel('Accept all cookies')).toBe('submit');
        expect(kindOfLabel('Inbox')).toBeNull();
        expect(kindOfLabel(undefined)).toBeNull();
    });

    it('classifies clicks by what is under them', () => {
        const app = { name: 'Mail' };
        expect(classifyAction('left_click', {}, { app, element: { label: 'Send' } })).toBe('send');
        expect(classifyAction('double_click', {}, { app, element: { label: 'Delete' } })).toBe(
            'delete',
        );
        expect(classifyAction('right_click', {}, { app, element: { label: 'Delete' } })).toBeNull();
        expect(classifyAction('left_click', {}, { app, element: null })).toBeNull();
    });

    it('classifies keys', () => {
        const app = { name: 'Slack' };
        expect(
            classifyAction(
                'key',
                { text: 'Return' },
                { app, focused: { secure: false, role: 'AXTextField' } },
            ),
        ).toBe('submit');
        expect(
            classifyAction(
                'key',
                { text: 'Return' },
                { app, focused: { secure: false, role: 'AXTextArea' } },
            ),
        ).toBeNull();
        expect(
            classifyAction(
                'key',
                { text: 'ctrl+Return' },
                { app, focused: { secure: false, role: 'AXTextArea' } },
            ),
        ).toBe('send');
        expect(classifyAction('key', { text: 'Delete' }, { app: { name: 'Finder' } })).toBe(
            'delete',
        );
        expect(classifyAction('key', { text: 'cmd+BackSpace' }, { app: { name: 'Finder' } })).toBe(
            'delete',
        );
        expect(classifyAction('key', { text: 'BackSpace' }, { app })).toBeNull();
        expect(
            classifyAction(
                'type',
                { text: 'hello\n' },
                { app, focused: { secure: false, role: 'AXTextField' } },
            ),
        ).toBe('submit');
        expect(classifyAction('type', { text: 'hello' }, { app })).toBeNull();
    });
});

describe('passwords', () => {
    it('never types into a password field or one the OS will not describe', () => {
        expect(keyboardRefusal('type', { text: 'x' }, { secure: true })).toMatch(/password/);
        expect(keyboardRefusal('type', { text: 'x' }, { secure: null })).toMatch(
            /could not confirm/,
        );
        expect(keyboardRefusal('type', { text: 'x' }, { secure: false })).toBeNull();
    });

    it('allows only moving away from a password field', () => {
        expect(keyboardRefusal('key', { text: 'Tab' }, { secure: true })).toBeNull();
        expect(keyboardRefusal('key', { text: 'shift+Tab' }, { secure: true })).toBeNull();
        expect(keyboardRefusal('key', { text: 'Escape' }, { secure: true })).toBeNull();
        expect(keyboardRefusal('key', { text: 'cmd+v' }, { secure: true })).toMatch(/password/);
        expect(keyboardRefusal('hold_key', { text: 'a' }, { secure: true })).toMatch(/password/);
        expect(keyboardRefusal('key', { text: 'Return' }, { secure: false })).toBeNull();
    });
});
