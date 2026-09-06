import { render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import { icdoCodeSegment } from '$lib/api';
import type { IcdoRecord } from '$lib/types';
import IcdoResultsTable from './IcdoResultsTable.svelte';

const common = {
	synonyms: [], related: [], notes: [], code_references: [], see_also: [], see_notes: [], includes: [], excludes: [], other_text: []
};
const hits: IcdoRecord[] = [
	{ ...common, code: '8000/0', preferred: 'Neoplasm', level: 'morphology', parent_code: null, base_morphology: '8000', specificity: null, behaviour: '0' },
	{ ...common, code: '8010/3', preferred: 'Carcinoma', level: 'morphology', parent_code: null, base_morphology: '8010', specificity: 'NOS', behaviour: '3' },
	{ ...common, code: 'C00', preferred: null, level: 'category', parent_code: null, base_morphology: null, specificity: null, behaviour: null },
	{ ...common, code: 'C00.1', preferred: 'Lip', level: 'leaf', parent_code: 'C00', base_morphology: null, specificity: null, behaviour: null }
];

describe('IcdoResultsTable', () => {
	it.each([
		{ edition: '3.2', axis: 'morphology' },
		{ edition: '4.0', axis: 'morphology' },
		{ edition: '4.0', axis: 'topography' }
	] as const)('renders encoded links, fallback terms, and levels for $edition $axis', (dataset) => {
		render(IcdoResultsTable, { dataset, hits });
		expect(screen.getByRole('link', { name: '8000/0' })).toHaveAttribute(
			'href',
			`/repositories/icdo/${dataset.edition}/${dataset.axis}/${icdoCodeSegment('8000/0')}`
		);
		expect(screen.getByText('No preferred term supplied')).toBeInTheDocument();
		expect(within(document.querySelector('tbody') as HTMLElement).getAllByText('morphology')).toHaveLength(2);
		expect(document.querySelector('thead')).toHaveClass('sticky', 'top-0', 'bg-card');
		expect(document.querySelector('thead th:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveStyle({ left: '0px' });
	});

	it('escapes every source-controlled ICD-O table field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const hostile = { ...hits[1], code: payload, preferred: payload, level: payload, behaviour: payload, specificity: payload } as unknown as IcdoRecord;
		const { container } = render(IcdoResultsTable, { dataset: { edition: '4.0', axis: 'topography' }, hits: [hostile] });
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(3);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
