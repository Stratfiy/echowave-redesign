/**
 * The words from the microphone land in the box beside what was typed.
 */
import { describe, expect, it } from 'vitest';

import { appendDictation, BARS, canDictate } from '../useDictation';

describe('appendDictation', () => {
    it('joins with one space, trims, and leaves an empty transcript alone', () => {
        expect(appendDictation('', '  namaste  ')).toBe('namaste');
        expect(appendDictation('Call Meera ', 'at four')).toBe('Call Meera at four');
        expect(appendDictation('Call Meera', '   ')).toBe('Call Meera');
    });
});

describe('canDictate', () => {
    it('is false where there is no recorder', () => {
        expect(canDictate()).toBe(false);
        expect(BARS).toBeGreaterThan(0);
    });
});
