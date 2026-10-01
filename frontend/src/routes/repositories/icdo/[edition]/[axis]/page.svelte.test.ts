import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Page from './+page.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://example.test/repositories/icdo/4.0/topography') },
	navigating: { to: null }
}));

it('renders an explicit fallback when an ICD-O record has no preferred term', () => {
	render(Page, {
		data: {
			edition: '4.0',
			axis: 'topography',
			initial: {
				query: '',
				offset: 0,
				size: 25,
				sort: 'source',
				filters: { level: [] },
				textFilters: {},
				result: {
					activation_identity: 'a'.repeat(64),
					serving_identity: 'b'.repeat(64),
					edition: '4.0',
					axis: 'topography',
					query: '',
					total: 1,
					limit: 25,
					offset: 0,
					sort: 'source',
					behaviour: [],
					level: [],
					column_text: {},
					hits: [
						{
							code: 'C34.9',
							preferred: null,
							level: 'leaf',
							parent_code: 'C34',
							base_morphology: null,
							specificity: null,
							behaviour: null,
							synonyms: [],
							related: [],
							notes: [],
							code_references: [],
							see_also: [],
							see_notes: [],
							includes: [],
							excludes: [],
							other_text: []
						}
					]
				}
			}
		} as never,
		params: { edition: '4.0', axis: 'topography' },
		form: null
	});

	expect(screen.getByText('No preferred term supplied')).toBeInTheDocument();
});
