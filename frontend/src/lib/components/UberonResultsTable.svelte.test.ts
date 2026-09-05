import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { UberonSearchHit } from '$lib/types';
import UberonResultsTable from './UberonResultsTable.svelte';

const hits: UberonSearchHit[] = [
	{ code: 'UBERON:0002048', label: 'lung', source: 'uberon', matched_synonym: null },
	{ code: 'CL:0000540', label: null, source: 'cl', matched_synonym: 'neuron' }
];

describe('UberonResultsTable', () => {
	it('preserves CURIE links, null labels, source display, and upstream order', () => {
		render(UberonResultsTable, { hits });
		expect(screen.getByRole('link', { name: 'UBERON:0002048' })).toHaveAttribute(
			'href', '/repositories/uberon/UBERON:0002048'
		);
		expect(screen.getByText('—')).toBeInTheDocument();
		expect(screen.getByText('Cell Ontology')).toBeInTheDocument();
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('UBERON:0002048');
		expect(screen.getByText('Filters and sorting apply only to the Uberon/CL rows loaded on this page.')).toBeVisible();
	});

	it('sorts and filters the loaded subset without navigation controls', async () => {
		render(UberonResultsTable, { hits });
		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Code' }));
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('CL:0000540');
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded ontology sources' }), {
			target: { value: 'cell ontology' }
		});
		expect(document.querySelectorAll('tbody tr')).toHaveLength(1);
		expect(screen.queryByRole('button', { name: /next page/i })).not.toBeInTheDocument();
	});

	it('escapes every source-controlled Uberon/CL field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(UberonResultsTable, {
			hits: [{ code: payload, label: payload, source: 'uberon', matched_synonym: payload }]
		});
		expect(within(container).getAllByText(payload)).toHaveLength(2);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
