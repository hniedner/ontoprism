import { describe, expect, it } from 'vitest';
import { cn } from './cn';

describe('cn', () => {
	it('joins truthy class values and drops falsy ones', () => {
		expect(cn('a', false, undefined, 'b', null)).toBe('a b');
	});

	it('resolves conflicting tailwind utilities in favor of the last one', () => {
		// tailwind-merge keeps the later padding, not both.
		expect(cn('p-2', 'p-4')).toBe('p-4');
	});

	it('supports conditional object syntax', () => {
		expect(cn({ 'text-red': true, 'text-blue': false })).toBe('text-red');
	});
});
