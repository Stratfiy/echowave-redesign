import { render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { Waveform } from '../Waveform';

describe('the waveform', () => {
    it('draws one bar per level, never shorter than a dot', () => {
        render(<Waveform levels={[0, 0.5, 1]} />);
        const bars = screen.getByRole('img', { name: 'Listening' }).querySelectorAll('span');
        expect(bars).toHaveLength(3);
        expect((bars[0] as HTMLElement).style.height).toBe('12%');
        expect((bars[2] as HTMLElement).style.height).toBe('100%');
    });
});
