import { describe, expect, it } from 'vitest';
import { nextDir, sortBy } from './sort';

interface Row {
	code: string;
	label: string | null;
}

const rows: Row[] = [
	{ code: 'C3', label: 'Beta' },
	{ code: 'C1', label: 'Alpha' },
	{ code: 'C2', label: null }
];

describe('sortBy', () => {
	it('sorts ascending by a string key', () => {
		const sorted = sortBy(rows, (r) => r.label, 'asc');
		expect(sorted.map((r) => r.code)).toEqual(['C1', 'C3', 'C2']);
	});

	it('sorts descending but keeps nulls last', () => {
		const sorted = sortBy(rows, (r) => r.label, 'desc');
		expect(sorted.map((r) => r.code)).toEqual(['C3', 'C1', 'C2']);
	});

	it('does not mutate the input', () => {
		const before = rows.map((r) => r.code);
		sortBy(rows, (r) => r.code, 'desc');
		expect(rows.map((r) => r.code)).toEqual(before);
	});
});

describe('nextDir', () => {
	it('toggles direction', () => {
		expect(nextDir('asc')).toBe('desc');
		expect(nextDir('desc')).toBe('asc');
	});
});
