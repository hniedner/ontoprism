import { describe, expect, it } from 'vitest';
import { empty, failed, ready, type LoadResult } from './load-result';

describe('LoadResult', () => {
	it('uses closed discriminants so blank, pending, failed, and successful data cannot overlap', () => {
		const states: LoadResult<{ id: string }>[] = [
			ready({ id: 'C3262' }),
			empty(),
			{ state: 'loading' },
			failed(503, 'FastAPI is unreachable')
		];

		expect(states).toEqual([
			{ state: 'ready', data: { id: 'C3262' } },
			{ state: 'empty' },
			{ state: 'loading' },
			{ state: 'error', status: 503, message: 'FastAPI is unreachable' }
		]);
	});

	it('rejects non-error statuses and blank error messages at construction', () => {
		expect(() => failed(399, 'redirect')).toThrow(RangeError);
		expect(() => failed(600, 'invalid')).toThrow(RangeError);
		expect(() => failed(503, '  ')).toThrow(TypeError);
	});
});
