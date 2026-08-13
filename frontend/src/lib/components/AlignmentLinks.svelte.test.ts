import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import AlignmentLinks from './AlignmentLinks.svelte';

describe('AlignmentLinks', () => {
	it('links every Uberon alignment to a validated NCIt detail route', () => {
		render(AlignmentLinks, {
			title: 'Aligned NCIt concepts',
			alignments: [
				{ code: 'C12468', system: 'ncit', version: '26.07d', predicate: 'http://www.w3.org/2004/02/skos/core#closeMatch', lifecycle: 'proposed' },
				{ code: 'C12345', system: 'ncit', version: '26.07d', predicate: 'http://www.w3.org/2004/02/skos/core#closeMatch', lifecycle: 'proposed' }
			]
		});

		expect(screen.getByRole('link', { name: 'Open aligned NCIt concept C12468' })).toHaveAttribute(
			'href',
			'/repositories/ncit/C12468'
		);
		expect(screen.getAllByText('Proposed close match')).toHaveLength(2);
	});

	it('links all reverse publisher assertions to Uberon details', () => {
		render(AlignmentLinks, {
			title: 'Aligned Uberon/CL concepts',
			alignments: ['UBERON:0000171', 'UBERON:0002048'].map((code) => ({
				code,
				system: 'uberon-cl' as const,
				version: '2026-06-19',
				predicate: 'http://www.w3.org/2004/02/skos/core#closeMatch' as const,
				lifecycle: 'proposed' as const
			}))
		});

		expect(screen.getByRole('link', { name: 'Open aligned Uberon/CL concept UBERON:0002048' })).toHaveAttribute(
			'href',
			'/repositories/uberon/UBERON:0002048'
		);
	});

	it('links slash-bearing ICD-O codes through one safe segment', () => {
		render(AlignmentLinks, {
			title: 'Aligned ICD-O-3.2 morphology codes',
			alignments: ['9751/1', '9751/3', '9752/1', '9753/1', '9754/3'].map((code) => ({
				code,
				system: 'icdo' as const,
				version: '3.2',
				predicate: 'http://www.w3.org/2004/02/skos/core#closeMatch' as const,
				lifecycle: 'proposed' as const
			}))
		});

		expect(screen.getByRole('link', { name: 'Open aligned ICD-O-3.2 morphology code 9754/3' })).toHaveAttribute(
			'href',
			'/repositories/icdo/3.2/morphology/OTc1NC8z'
		);
		expect(screen.getAllByText('Proposed close match')).toHaveLength(5);
	});

	it('renders the actual predicate and lifecycle and a source-neutral empty state', () => {
		const { rerender } = render(AlignmentLinks, {
			title: 'Alignments',
			alignments: [
				{ code: 'C1', system: 'ncit', version: '26.07d', predicate: 'http://www.w3.org/2004/02/skos/core#exactMatch', lifecycle: 'validated' }
			]
		});

		expect(screen.getByText('Validated exact match')).toBeInTheDocument();
		rerender({ title: 'Alignments', alignments: [] });
		expect(screen.getByText('No alignments.')).toBeInTheDocument();
	});
});
