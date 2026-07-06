import { describe, expect, it } from 'vitest';
import { flattenSparql } from './sparql';

describe('flattenSparql', () => {
	it('projects SELECT vars in order, filling unbound cells with empty string', () => {
		const table = flattenSparql({
			head: { vars: ['s', 'label'] },
			results: {
				bindings: [
					{ s: { value: 'C3262' }, label: { value: 'Neoplasm' } },
					{ s: { value: 'C9305' } } // label unbound
				]
			}
		});
		expect(table.columns).toEqual(['s', 'label']);
		expect(table.rows).toEqual([
			['C3262', 'Neoplasm'],
			['C9305', '']
		]);
	});

	it('renders an ASK boolean as a single result cell', () => {
		const table = flattenSparql({ boolean: true });
		expect(table.columns).toEqual(['result']);
		expect(table.rows).toEqual([['true']]);
		expect(table.boolean).toBe(true);
	});

	it('returns empty columns and rows for an empty result', () => {
		const table = flattenSparql({ head: { vars: [] }, results: { bindings: [] } });
		expect(table.columns).toEqual([]);
		expect(table.rows).toEqual([]);
	});

	it('defaults to empty columns/rows when head and results are absent', () => {
		const table = flattenSparql({});
		expect(table.columns).toEqual([]);
		expect(table.rows).toEqual([]);
	});
});
