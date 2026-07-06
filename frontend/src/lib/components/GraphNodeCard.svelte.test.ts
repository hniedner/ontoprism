import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import GraphNodeCard from './GraphNodeCard.svelte';
import type { NodeAttrs } from '$lib/graph/neighborhood-graph';

const selected: NodeAttrs = {
	code: 'C3262',
	label: 'Neoplasm',
	semanticType: 'Neoplastic Process',
	degree: 4,
	community: 1,
	expanded: false
};

describe('GraphNodeCard', () => {
	it('prompts to click a node when nothing is selected', () => {
		render(GraphNodeCard, { selected: null, expanding: false, onexpand: vi.fn(), onopen: vi.fn() });
		expect(screen.getByText(/Click a node to inspect it/)).toBeInTheDocument();
	});

	it('renders the selected node metadata (label, code, type, degree, community)', () => {
		render(GraphNodeCard, { selected, expanding: false, onexpand: vi.fn(), onopen: vi.fn() });
		expect(screen.getByText('Neoplasm')).toBeInTheDocument();
		expect(screen.getByText('C3262')).toBeInTheDocument();
		expect(screen.getByText('Neoplastic Process')).toBeInTheDocument();
		expect(screen.getByText('4')).toBeInTheDocument();
		// Community is displayed 1-indexed (#community+1).
		expect(screen.getByText('#2')).toBeInTheDocument();
	});

	it('calls onexpand / onopen with the node code', async () => {
		const onexpand = vi.fn();
		const onopen = vi.fn();
		render(GraphNodeCard, { selected, expanding: false, onexpand, onopen });
		screen.getByRole('button', { name: 'Expand node' }).click();
		screen.getByRole('button', { name: 'Open concept →' }).click();
		await tick();
		expect(onexpand).toHaveBeenCalledWith('C3262');
		expect(onopen).toHaveBeenCalledWith('C3262');
	});

	it('disables and relabels the expand button for an already-expanded node', () => {
		render(GraphNodeCard, {
			selected: { ...selected, expanded: true },
			expanding: false,
			onexpand: vi.fn(),
			onopen: vi.fn()
		});
		expect(screen.getByRole('button', { name: 'Expanded' })).toBeDisabled();
	});

	it('disables the expand button while an expansion is in flight', () => {
		render(GraphNodeCard, { selected, expanding: true, onexpand: vi.fn(), onopen: vi.fn() });
		expect(screen.getByRole('button', { name: 'Expand node' })).toBeDisabled();
	});

	it('omits the semantic-type chip for a node without one', () => {
		render(GraphNodeCard, {
			selected: { ...selected, semanticType: null },
			expanding: false,
			onexpand: vi.fn(),
			onopen: vi.fn()
		});
		expect(screen.queryByText('Neoplastic Process')).not.toBeInTheDocument();
		// Metadata still renders.
		expect(screen.getByText('Neoplasm')).toBeInTheDocument();
	});

	it('defaults degree to 0 and community to #1 when those attributes are absent', () => {
		render(GraphNodeCard, {
			selected: { code: 'C1', label: 'Bare', semanticType: null },
			expanding: false,
			onexpand: vi.fn(),
			onopen: vi.fn()
		});
		expect(screen.getByText('0')).toBeInTheDocument(); // degree ?? 0
		expect(screen.getByText('#1')).toBeInTheDocument(); // (community ?? 0) + 1
	});
});
