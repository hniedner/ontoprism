import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { SvelteSet } from 'svelte/reactivity';
import GraphSidePanel from './GraphSidePanel.svelte';
import type { AnalyticsSummary } from '$lib/graph/neighborhood-graph';

const stats: AnalyticsSummary = {
	communityCount: 2,
	topByDegree: [
		{ code: 'C1', label: 'Alpha', degree: 5 },
		{ code: 'C2', label: 'Beta', degree: 3 }
	],
	topByBetweenness: [
		{ code: 'C1', label: 'Alpha', betweenness: 0.42 },
		{ code: 'C3', label: 'Gamma', betweenness: 0 }
	]
};

function setup(overrides: Record<string, unknown> = {}) {
	const onexpand = vi.fn();
	const onopen = vi.fn();
	const onfocus = vi.fn();
	const ontoggletype = vi.fn();
	render(GraphSidePanel, {
		selected: null,
		expanding: false,
		stats,
		nodeCount: 12,
		edgeCount: 20,
		semanticTypes: ['Disease', 'Gene'],
		hiddenTypes: new SvelteSet<string>(),
		onexpand,
		onopen,
		onfocus,
		ontoggletype,
		...overrides
	});
	return { onexpand, onopen, onfocus, ontoggletype };
}

describe('GraphSidePanel', () => {
	it('shows the network stats', () => {
		setup();
		expect(screen.getByText('12')).toBeInTheDocument(); // nodes
		expect(screen.getByText('20')).toBeInTheDocument(); // edges
	});

	it('lists the most-connected nodes with their degree', () => {
		setup();
		// Alpha is both most-connected and a bridge, so it appears in both lists.
		expect(screen.getAllByRole('button', { name: 'Alpha' }).length).toBeGreaterThanOrEqual(1);
		expect(screen.getByText('5')).toBeInTheDocument();
	});

	it('shows key bridges only when a node has non-zero betweenness', () => {
		setup();
		expect(screen.getByText('Key bridges')).toBeInTheDocument();
		expect(screen.getByText('0.42')).toBeInTheDocument();
		// The zero-betweenness node is filtered out of the bridges list.
		expect(screen.queryByRole('button', { name: 'Gamma' })).not.toBeInTheDocument();
	});

	it('hides the bridges section when all betweenness is zero', () => {
		setup({ stats: { ...stats, topByBetweenness: [{ code: 'C3', label: 'Gamma', betweenness: 0 }] } });
		expect(screen.queryByText('Key bridges')).not.toBeInTheDocument();
	});

	it('focuses a node when its ranked-list entry is clicked', async () => {
		const { onfocus } = setup();
		screen.getAllByRole('button', { name: 'Alpha' })[0].click();
		await tick();
		expect(onfocus).toHaveBeenCalledWith('C1');
	});

	it('renders semantic-type filter chips and toggles them', async () => {
		const { ontoggletype } = setup();
		const chip = screen.getByRole('button', { name: 'Gene' });
		chip.click();
		await tick();
		expect(ontoggletype).toHaveBeenCalledWith('Gene');
	});

	it('does not show the type filter with only one type', () => {
		setup({ semanticTypes: ['Gene'] });
		expect(screen.queryByText('Filter by type')).not.toBeInTheDocument();
	});

	it('renders the edge-kind legend', () => {
		setup();
		expect(screen.getByText('subClassOf')).toBeInTheDocument();
		expect(screen.getByText('role')).toBeInTheDocument();
		expect(screen.getByText('cde-concept')).toBeInTheDocument();
	});

	it('marks hidden filter types with a line-through class and Show title', () => {
		setup({ hiddenTypes: new SvelteSet(['Gene']) });
		const chips = screen.getAllByRole('button');
		// The 'Show' title appears on hidden chips.
		const hiddenChip = screen.getByTitle('Show');
		expect(hiddenChip).toHaveTextContent('Gene');
		expect(chips.filter((c) => c.title === 'Hide')).toHaveLength(1);
	});

	it('omits the most-connected section when there are no ranked nodes', () => {
		setup({
			stats: { communityCount: 0, topByDegree: [], topByBetweenness: [] },
			semanticTypes: []
		});
		expect(screen.queryByText('Most connected')).not.toBeInTheDocument();
		expect(screen.queryByText('Key bridges')).not.toBeInTheDocument();
	});

	it('renders a single semantic type without the filter heading', () => {
		setup({ semanticTypes: ['Gene'] });
		expect(screen.queryByText('Filter by type')).not.toBeInTheDocument();
		// With one type no filter chips are rendered; the edge legend is still present.
		expect(screen.getByText('subClassOf')).toBeInTheDocument();
	});

	it('does not render filter chips when semantic type array is empty', () => {
		setup({ semanticTypes: [], stats: { communityCount: 0, topByDegree: [], topByBetweenness: [] } });
		expect(screen.queryByText('Filter by type')).not.toBeInTheDocument();
	});
});
