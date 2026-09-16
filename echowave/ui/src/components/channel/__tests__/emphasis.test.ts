import { describe, expect, it } from 'vitest';

import { emphasisTokens } from '@/components/channel/emphasis';

/** The defect: a bot's reply printed `**Wednesday Web Drop**` with the
 *  asterisks on screen, because nothing rendered the markdown a model writes
 *  by default and no markdown library is installed. */
describe('emphasisTokens', () => {
    it('reads bold', () => {
        expect(emphasisTokens('**Mobbin**')).toEqual([{ text: 'Mobbin', emphasis: 'bold' }]);
    });

    it('reads italic', () => {
        expect(emphasisTokens('*note*')).toEqual([{ text: 'note', emphasis: 'italic' }]);
    });

    it('reads inline code', () => {
        expect(emphasisTokens('`GMAIL_SEND_EMAIL`')).toEqual([
            { text: 'GMAIL_SEND_EMAIL', emphasis: 'code' },
        ]);
    });

    it('reads bold as bold, not as italic with a stray asterisk', () => {
        // `**x**` also matches the italic pattern. Reading it that way leaves
        // an asterisk at each end, which is worse than not rendering at all.
        expect(emphasisTokens('**x**')).toEqual([{ text: 'x', emphasis: 'bold' }]);
    });

    it('keeps the text around it', () => {
        expect(emphasisTokens('1. **Web Drop** — Mobbin')).toEqual([
            { text: '1. ', emphasis: null },
            { text: 'Web Drop', emphasis: 'bold' },
            { text: ' — Mobbin', emphasis: null },
        ]);
    });

    it('reads several on one line', () => {
        const out = emphasisTokens('**a** and **b**');
        expect(out.filter((t) => t.emphasis === 'bold').map((t) => t.text)).toEqual(['a', 'b']);
    });

    it('leaves plain text alone', () => {
        expect(emphasisTokens('no markup here')).toEqual([
            { text: 'no markup here', emphasis: null },
        ]);
    });

    it('leaves an unclosed marker as the text it is', () => {
        // Somebody writing about asterisks should see asterisks.
        expect(emphasisTokens('2 * 3 = 6')).toEqual([{ text: '2 * 3 = 6', emphasis: null }]);
        expect(emphasisTokens('**unclosed')).toEqual([{ text: '**unclosed', emphasis: null }]);
    });

    it('leaves an empty marker alone', () => {
        expect(emphasisTokens('****')).toEqual([{ text: '****', emphasis: null }]);
    });

    it('does not run emphasis across a line break', () => {
        // A stray asterisk on one line must not reach down and emphasise the
        // next one; the reply would reflow from a single typo.
        const out = emphasisTokens('*one\ntwo*');
        expect(out).toEqual([{ text: '*one\ntwo*', emphasis: null }]);
    });

    it('is empty for empty input', () => {
        expect(emphasisTokens('')).toEqual([]);
    });

    it('never invents markup from an email subject line', () => {
        // These messages relay text written by strangers. Nothing here may
        // turn one into a tag: every branch returns text React escapes.
        const hostile = '<img src=x onerror=alert(1)> <b>bold?</b>';
        expect(emphasisTokens(hostile)).toEqual([{ text: hostile, emphasis: null }]);
    });
});
