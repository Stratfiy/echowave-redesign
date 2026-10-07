/**
 * Phone numbers in E.164 (+919876543210), India first.
 *
 * The address book holds numbers in every shape -- "098765 43210",
 * "+91 98765-43210", "9876543210", "0091 98765 43210" -- and the same person
 * must come out as the same number whichever one was typed, or a re-sync
 * sees a change that is not one. A bare 10-digit Indian mobile gets +91,
 * like the care stream does for reminder numbers.
 */

const DIAL_CODES: Record<string, string> = {
    IN: '91',
    US: '1',
    CA: '1',
    GB: '44',
    AE: '971',
    SG: '65',
    AU: '61',
};

export function toE164(raw: string | null | undefined, defaultCountry = 'IN'): string | null {
    if (!raw) return null;
    const trimmed = raw.trim();
    if (!trimmed) return null;
    // Extensions ("x123", "ext. 4") are not part of the number.
    const main = trimmed.split(/(?:ext\.?|x|#|;|,)/i)[0];
    const plus = main.trim().startsWith('+');
    let digits = main.replace(/\D/g, '');
    if (!digits) return null;
    if (plus) return valid(`+${digits}`);
    if (digits.startsWith('00')) return valid(`+${digits.slice(2)}`);
    const code = DIAL_CODES[defaultCountry.toUpperCase()] ?? '91';
    if (code === '91') {
        // 0 trunk prefix on Indian numbers ("09876543210").
        if (digits.length === 11 && digits.startsWith('0')) digits = digits.slice(1);
        if (digits.length === 12 && digits.startsWith('91')) return valid(`+${digits}`);
        if (digits.length === 10 && /^[6-9]/.test(digits)) return valid(`+91${digits}`);
        // Landlines with an STD code ("080 1234 5678" -> 10 digits after 0).
        if (digits.length === 10) return valid(`+91${digits}`);
        return null;
    }
    if (digits.startsWith('0')) digits = digits.replace(/^0+/, '');
    if (digits.startsWith(code) && digits.length > 10) return valid(`+${digits}`);
    return valid(`+${code}${digits}`);
}

function valid(e164: string): string | null {
    // E.164: a plus, a non-zero country digit, at most 15 digits in all.
    return /^\+[1-9]\d{7,14}$/.test(e164) ? e164 : null;
}

/** "+91 98765 43210" for display; the stored value stays E.164. */
export function displayPhone(e164: string): string {
    const m = /^\+91(\d{5})(\d{5})$/.exec(e164);
    if (m) return `+91 ${m[1]} ${m[2]}`;
    return e164;
}
