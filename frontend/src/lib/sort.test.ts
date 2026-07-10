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

	it('treats two null keys as equal (stable)', () => {
		const bothNull: Row[] = [
			{ code: 'C1', label: null },
			{ code: 'C2', label: null }
		];
		expect(sortBy(bothNull, (r) => r.label, 'asc').map((r) => r.code)).toEqual(['C1', 'C2']);
	});

	it('keeps equal keys in their original order', () => {
		const dupes: Row[] = [
			{ code: 'C1', label: 'Same' },
			{ code: 'C2', label: 'Same' }
		];
		expect(sortBy(dupes, (r) => r.label, 'desc').map((r) => r.code)).toEqual(['C1', 'C2']);
	});

	it('sorts by a numeric key', () => {
		const nums = [{ n: 3 }, { n: 1 }, { n: 2 }];
		expect(sortBy(nums, (r) => r.n, 'asc').map((r) => r.n)).toEqual([1, 2, 3]);
		expect(sortBy(nums, (r) => r.n, 'desc').map((r) => r.n)).toEqual([3, 2, 1]);
	});

	it('handles mixed null and non-null keys where only bv is null', () => {
		const items = [
			{ v: 'Beta' },
			{ v: null },
			{ v: 'Alpha' }
		];
		expect(sortBy(items, (r) => r.v, 'asc').map((r) => r.v)).toEqual(['Alpha', 'Beta', null]);
		expect(sortBy(items, (r) => r.v, 'desc').map((r) => r.v)).toEqual(['Beta', 'Alpha', null]);
	});
});

describe('nextDir', () => {
	it('toggles direction', () => {
		expect(nextDir('asc')).toBe('desc');
		expect(nextDir('desc')).toBe('asc');
	});
});
