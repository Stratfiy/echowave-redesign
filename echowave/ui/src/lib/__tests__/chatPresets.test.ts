import { describe, expect, it } from 'vitest';

import { CHAT_PRESETS, formatTokens, labelFor, replyCostLabel, replyCredits } from '../chatPresets';

describe('the picker words', () => {
    it('is three words, Advanced gone', () => {
        expect(CHAT_PRESETS.map((p) => p.slug)).toEqual(['everyday', 'smart', 'deep']);
    });

    it('names a choice the menu carries, and reads one it no longer does', () => {
        const vendors = [{ id: 'openai', label: 'OpenAI', models: [{ slug: 'model:openai/gpt-5', label: 'GPT-5' }] }];
        expect(labelFor('', vendors)).toBe('Agent’s own');
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

describe('what a reply costs (D-1)', () => {
    it('says 1 cr/reply on a standard model and an estimate on a premium one', () => {
        expect(replyCostLabel(1)).toBe('1 cr/reply');
        expect(replyCostLabel(5)).toBe('≈5 cr/reply');
        expect(replyCostLabel(undefined)).toBeNull();
        expect(replyCostLabel(null)).toBeNull();
    });

    it('reads the figures off the menu the API sent', () => {
        const vendors = [
            { id: 'openai', label: 'OpenAI', models: [{ slug: 'model:openai/gpt-5', label: 'GPT-5', reply_credits: 5 }, { slug: 'model:openai/gpt-4.1', label: 'GPT-4.1' }] },
        ];
        expect(replyCredits([{ slug: 'everyday', reply_credits: 1 }, { slug: 'deep' }], vendors)).toEqual({
            everyday: 1,
            'model:openai/gpt-5': 5,
        });
    });
});
