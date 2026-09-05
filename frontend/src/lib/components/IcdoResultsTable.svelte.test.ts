import { fireEvent, render, screen, within } from '@testing-library/svelte';
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
		expect(screen.getAllByText('morphology')).toHaveLength(2);
		expect(screen.getByText('Filters and sorting apply only to the ICD-O records loaded on this page.')).toBeVisible();
	});

	it('sorts and filters all four merged record variants on the loaded page', async () => {
		render(IcdoResultsTable, { dataset: { edition: '4.0', axis: 'topography' }, hits });
		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Code' }));
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded ICD-O levels' }), {
			target: { value: 'leaf' }
		});
		expect(document.querySelectorAll('tbody tr')).toHaveLength(1);
		expect(screen.getByRole('link', { name: 'C00.1' })).toBeInTheDocument();
	});

	it('escapes every source-controlled ICD-O table field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const source = hits[3];
		if (source.level !== 'leaf') throw new Error('Expected the leaf fixture');
		const hostile: IcdoRecord = { ...source, code: payload, preferred: payload };
		const { container } = render(IcdoResultsTable, { dataset: { edition: '4.0', axis: 'topography' }, hits: [hostile] });
		expect(within(container).getAllByText(payload)).toHaveLength(2);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
