/**
 * What Decibyl may touch on this computer, and what needs a person first.
 *
 * Three rules, in the order the loop applies them:
 *
 * 1. **Never touch.** System security dialogs and password managers are out
 *    of reach whatever the person's list says: no screenshot of them is
 *    taken, no key goes to them. A person cannot put them on the list, so a
 *    mis-tap in Settings cannot either.
 * 2. **Only the apps the person picked.** Default deny. An app that is not on
 *    the list is not silently skipped: the model is told its name and that it
 *    is not allowed, and the bar says so, so the absence is visible.
 * 3. **Ask before it counts.** A click on a Send / Pay / Delete / Submit
 *    control, an Enter that would send, a delete key in a file manager --
 *    and anything the model itself declared consequential -- is held and
 *    turned into an approval card in the web app.
 */

import type { ConsequentialKind, ElementInfo, FocusedElement, FrontApp } from './types';

export interface AppRule {
    /** The person ticked this app. */
    allowed: boolean;
}

export type AppRules = Record<string, AppRule>;

/** Lower-case, no ".app"/".exe", no surrounding space: the key in AppRules. */
export function appKey(name: string): string {
    return name
        .trim()
        .toLowerCase()
        .replace(/\.(app|exe)$/i, '')
        .replace(/\s+/g, ' ');
}

/**
 * System security surfaces and password managers. Matched against the app
 * name and its id (bundle id / executable), case-insensitively.
 */
export const NEVER_TOUCH_APPS: readonly string[] = [
    // Decibyl itself: its settings hold the list of allowed apps
    'decibyl',
    'com.decibyl.desktop',
    'electron',
    // macOS: the authentication prompt, Touch ID sheet, keychain, settings
    'securityagent',
    'coreautha',
    'coreauthuiagent',
    'keychain access',
    'com.apple.keychainaccess',
    'passwords',
    'com.apple.passwords',
    'system settings',
    'system preferences',
    'com.apple.systempreferences',
    'loginwindow',
    // Windows: UAC, the credential prompt, the lock screen, Windows Security
    'consent',
    'credentialuibroker',
    'logonui',
    'windows security',
    'sechealthui',
    'lockapp',
    // Password managers
    '1password',
    'bitwarden',
    'lastpass',
    'dashlane',
    'keepass',
    'keepassxc',
    'keeper',
    'keeper password manager',
    'enpass',
    'nordpass',
    'proton pass',
];

/** Window titles that mark a system credential prompt inside another app. */
const SECURITY_TITLE = [
    /user account control/i,
    /wants to make changes/i,
    /(enter|type) (your |the )?(administrator |admin |login |keychain )?password/i,
    /is trying to (unlock|install|modify|make changes)/i,
    /windows security/i,
    /touch id/i,
    /windows hello/i,
];

export function isNeverTouch(app: FrontApp | null): boolean {
    if (!app) return false;
    const keys = [app.name, app.id ?? ''].map(appKey).filter(Boolean);
    for (const key of keys) {
        for (const banned of NEVER_TOUCH_APPS) {
            if (key === banned || key.endsWith(`.${banned}`) || key.endsWith(`\\${banned}`)) {
                return true;
            }
        }
    }
    const title = app.windowTitle ?? '';
    return SECURITY_TITLE.some((re) => re.test(title));
}

export function isAllowed(app: FrontApp | null, rules: AppRules): boolean {
    if (!app || isNeverTouch(app)) return false;
    return rules[appKey(app.name)]?.allowed === true;
}

/** Whether a name may go on the list at all (the settings screen asks). */
export function canBeAllowed(name: string): boolean {
    return !isNeverTouch({ name });
}

// ---------------------------------------------------------------------------
// Consequential actions
// ---------------------------------------------------------------------------

const LABELS: Array<[ConsequentialKind, RegExp]> = [
    [
        'pay',
        /\b(pay|pay now|buy|buy now|purchase|checkout|check out|place order|order now|transfer|donate|subscribe|top ?up|recharge)\b/i,
    ],
    [
        'delete',
        /\b(delete|remove|trash|erase|discard|empty (bin|trash)|uninstall|move to (bin|trash))\b/i,
    ],
    ['send', /\b(send|post|reply|reply all|tweet|share|publish|forward|invite)\b/i],
    [
        'submit',
        /\b(submit|confirm|sign|e-?sign|agree|accept|apply|book|register|file|authori[sz]e|approve|continue to payment)\b/i,
    ],
];

/** A labelled control's kind, or null. */
export function kindOfLabel(label: string | undefined): ConsequentialKind | null {
    if (!label) return null;
    for (const [kind, re] of LABELS) {
        if (re.test(label)) return kind;
    }
    return null;
}

const FILE_MANAGERS = new Set(['finder', 'explorer', 'file explorer', 'windows explorer']);
/** Roles where a plain Enter is a new line, not a send. */
const MULTILINE_ROLES = /^(axtextarea|document|edit\.multiline|textarea)$/i;
const ENTER = /^(return|enter|kp_enter)$/i;
const DELETE = /^(delete|backspace|back_space|del)$/i;
const SUBMIT_MODIFIERS = /^(ctrl|control|cmd|command|super|meta)$/i;

function splitCombo(combo: string): { keys: string[]; modifiers: string[] } {
    const parts = combo
        .split('+')
        .map((p) => p.trim())
        .filter(Boolean);
    const modifiers = parts.filter((p) =>
        /^(ctrl|control|cmd|command|super|meta|alt|option|shift)$/i.test(p),
    );
    return { keys: parts.filter((p) => !modifiers.includes(p)), modifiers };
}

export interface ClassifyContext {
    app: FrontApp | null;
    /** What is under the click, when the driver could read it. */
    element?: ElementInfo | null;
    focused?: FocusedElement | null;
}

/**
 * Whether this one computer action would send, pay, delete or submit.
 * The model's own `request_approval` covers what this cannot see (a button
 * with no accessible label); this is the backstop for what it forgets.
 */
export function classifyAction(
    name: string,
    input: Record<string, unknown>,
    ctx: ClassifyContext,
): ConsequentialKind | null {
    if (name === 'left_click' || name === 'double_click' || name === 'triple_click') {
        return kindOfLabel(ctx.element?.label);
    }
    if (name === 'key') {
        const { keys, modifiers } = splitCombo(String(input.text ?? ''));
        const app = appKey(ctx.app?.name ?? '');
        if (keys.some((k) => ENTER.test(k))) {
            if (modifiers.some((m) => SUBMIT_MODIFIERS.test(m))) return 'send';
            if (MULTILINE_ROLES.test(ctx.focused?.role ?? '')) return null;
            return 'submit';
        }
        // In a file manager Delete (or Cmd+Backspace, Shift+Delete) removes
        // files; in a text field it removes a character, which is fine.
        if (keys.some((k) => DELETE.test(k)) && FILE_MANAGERS.has(app)) return 'delete';
        return null;
    }
    if (name === 'type') {
        const text = String(input.text ?? '');
        if (/\n$/.test(text) && !MULTILINE_ROLES.test(ctx.focused?.role ?? '')) return 'submit';
        return null;
    }
    return null;
}

// ---------------------------------------------------------------------------
// Passwords
// ---------------------------------------------------------------------------

/** Keys that move focus or close things; harmless in a password field. */
const NAVIGATION_KEYS =
    /^(tab|escape|esc|up|down|left|right|home|end|page_up|page_down|pageup|pagedown)$/i;

/**
 * Whether a keyboard action may go to the focused element. A `type` needs a
 * field known not to be secure; a key press is refused only in a field
 * known to be secure (Tab and Escape still let the model move away).
 */
export function keyboardRefusal(
    name: 'type' | 'key' | 'hold_key',
    input: Record<string, unknown>,
    focused: FocusedElement,
): string | null {
    if (name === 'type') {
        if (focused.secure === true) {
            return 'That is a password field. Decibyl never types into password fields; ask the person to type it themselves.';
        }
        if (focused.secure === null) {
            return 'Decibyl could not confirm that the focused field is not a password field, so it will not type there. Ask the person, or click into a normal text field first.';
        }
        return null;
    }
    if (focused.secure === true) {
        const { keys } = splitCombo(String(input.text ?? ''));
        if (keys.length > 0 && keys.every((k) => NAVIGATION_KEYS.test(k))) return null;
        return 'A password field has focus. Decibyl never types into password fields; press Tab to move away or ask the person.';
    }
    return null;
}
