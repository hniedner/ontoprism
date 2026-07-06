import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import RelationshipList from './RelationshipList.svelte';
import type { Relationship } from '$lib/types';

const rels: Relationship[] = [
	{ relation: 'R123', relation_label: 'has_finding', target: { code: 'C1', label: 'Fever' } },
	{ relation: 'R456', relation_label: null, target: { code: 'C2', label: null } }
];

describe('RelationshipList', () => {
	it('renders the title with the item count', () => {
		render(RelationshipList, { title: 'Roles', items: rels });
		expect(screen.getByText('Roles')).toBeInTheDocument();
		expect(screen.getByText('2')).toBeInTheDocument();
	});

	it('renders each relationship with a label falling back to the relation code', () => {
		render(RelationshipList, { title: 'Roles', items: rels });
		expect(screen.getByText('has_finding')).toBeInTheDocument();
		expect(screen.getByText('R456')).toBeInTheDocument(); // null label → relation code
	});

	it('links the target to its NCIt concept page, falling back to code for the label', () => {
		render(RelationshipList, { title: 'Roles', items: rels });
		const link = screen.getByRole('link', { name: 'Fever' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C1');
		// Second target has a null label → shows the code as link text.
		expect(screen.getByRole('link', { name: 'C2' })).toBeInTheDocument();
	});

	it('shows an empty state when there are no items', () => {
		render(RelationshipList, { title: 'Associations', items: [] });
		expect(screen.getByText('None.')).toBeInTheDocument();
		expect(screen.getByText('0')).toBeInTheDocument();
	});
});
