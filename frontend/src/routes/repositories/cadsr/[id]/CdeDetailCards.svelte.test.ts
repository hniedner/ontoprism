import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { CdeDetail } from '$lib/types';
import CdeDetailCards from './CdeDetailCards.svelte';

vi.mock('$lib/api', () => ({ similarCdes: vi.fn(() => new Promise(() => {})) }));

function cdeWithLongLists(): CdeDetail {
	return {
		public_id: '6686721',
		version: '1.0',
		short_name: 'PCI',
		long_name: 'Percutaneous Coronary Intervention',
		context: 'NCIP',
		datatype: 'CHARACTER',
		definition: 'A representative caDSR detail.',
		workflow_status: 'RELEASED',
		registration_status: 'Standard',
		value_domain_type: 'Enumerated',
		concepts: Array.from({ length: 8 }, (_, index) => ({
			concept_code: `C${index + 1}`,
			concept_name: `Concept ${index + 1}`,
			concept_type: 'objectClass',
			is_primary: index === 0
		})),
		permissible_values: Array.from({ length: 8 }, (_, index) => ({
			value: `Value ${index + 1}`,
			meaning: `Meaning ${index + 1}`,
			meaning_code: `M${index + 1}`
		}))
	};
}

describe('CdeDetailCards', () => {
	it('aligns cards to their own content height', () => {
		const { container } = render(CdeDetailCards, { cde: cdeWithLongLists() });

		expect(container.querySelector('.grid')).toHaveClass('items-start');
	});

	it.each([
		['NCIt concepts', 'Concept 7'],
		['Permissible values', 'Value 7']
	])('previews six %s and explicitly reveals the full list', async (listName, hiddenItem) => {
		render(CdeDetailCards, { cde: cdeWithLongLists() });

		expect(screen.queryByText(hiddenItem)).not.toBeInTheDocument();
		const showAll = screen.getByRole('button', { name: `Show all 8 ${listName}` });
		await fireEvent.click(showAll);
		expect(screen.getByText(hiddenItem)).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: `Show fewer ${listName}` })
		).toBeInTheDocument();
	});
});
