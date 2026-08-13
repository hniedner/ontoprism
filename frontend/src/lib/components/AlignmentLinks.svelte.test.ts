import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import AlignmentLinks from './AlignmentLinks.svelte';

describe('AlignmentLinks', () => {
	it('links every Uberon alignment to a validated NCIt detail route', () => {
		render(AlignmentLinks, {
			title: 'Aligned NCIt concepts',
			alignments: [
				{ code: 'C12468', system: 'ncit', version: '26.07d', predicate: 'closeMatch', lifecycle: 'proposed' },
				{ code: 'C12345', system: 'ncit', version: '26.07d', predicate: 'closeMatch', lifecycle: 'proposed' }
			]
		});

		expect(screen.getByRole('link', { name: 'Open aligned NCIt concept C12468' })).toHaveAttribute(
			'href',
			'/repositories/ncit/C12468'
		);
		expect(screen.getAllByText('Proposed close alignment')).toHaveLength(2);
	});

	it('links all reverse publisher assertions to Uberon details', () => {
		render(AlignmentLinks, {
			title: 'Aligned Uberon/CL concepts',
			alignments: ['UBERON:0000171', 'UBERON:0002048'].map((code) => ({
				code,
				system: 'uberon-cl' as const,
				version: '2026-06-19',
				predicate: 'closeMatch',
				lifecycle: 'proposed'
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
				predicate: 'closeMatch',
				lifecycle: 'proposed'
			}))
		});

		expect(screen.getByRole('link', { name: 'Open aligned ICD-O-3.2 morphology code 9754/3' })).toHaveAttribute(
			'href',
			'/repositories/icdo/3.2/morphology/OTc1NC8z'
		);
		expect(screen.getAllByText('Proposed close alignment')).toHaveLength(5);
	});
});
