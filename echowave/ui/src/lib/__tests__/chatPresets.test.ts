import { describe, expect, it } from 'vitest';

import { CHAT_PRESETS, formatTokens, labelFor } from '../chatPresets';

describe('the picker words', () => {
    it('is three words, Advanced gone', () => {
        expect(CHAT_PRESETS.map((p) => p.slug)).toEqual(['everyday', 'smart', 'deep']);
    });

    it('names a choice the menu carries, and reads one it no longer does', () => {
        const vendors = [{ id: 'openai', label: 'OpenAI', models: [{ slug: 'model:openai/gpt-5', label: 'GPT-5' }] }];
        expect(labelFor('', vendors)).toBe('Bot’s own');
        expect(labelFor('deep', vendors)).toBe('Deep');
        expect(labelFor('model:openai/gpt-5', vendors)).toBe('GPT-5');
        expect(labelFor('advanced', vendors)).toBe('Advanced');
        expect(labelFor('model:google/gemini-3.5-flash', vendors)).toBe('gemini-3.5-flash');
    });
});

describe('the meter numbers', () => {
    it('rounds the way a gauge reads', () => {
        expect(formatTokens(0)).toBe('0');
        expect(formatTokens(800)).toBe('800');
        expect(formatTokens(3_250)).toBe('3.3k');
        expect(formatTokens(16_000)).toBe('16k');
        expect(formatTokens(128_000)).toBe('128k');
    });
});
