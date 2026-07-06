import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import { tick } from 'svelte';
import NeighborhoodGraph from './NeighborhoodGraph.svelte';
import type { Neighborhood } from '$lib/types';

const goto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...a: unknown[]) => goto(...a) }));

const graph: Neighborhood = {
	center: 'C3262',
	nodes: [
		{ code: 'C3262', label: 'Neoplasm', semantic_type: 'Neoplastic Process' },
		{ code: 'C9305', label: 'Malignant Neoplasm', semantic_type: null }
	],
	edges: [
		{
			source: 'C9305',
			target: 'C3262',
			relation: 'subClassOf',
			relation_label: 'is a',
			kind: 'subClassOf'
		}
	]
};

beforeEach(() => goto.mockClear());

describe('NeighborhoodGraph', () => {
	it('summarizes the node and edge counts', () => {
		render(NeighborhoodGraph, { graph });
		expect(screen.getByText('2 nodes · 1 edges')).toBeInTheDocument();
	});

	it('renders a clickable node per graph node, labelled by code', () => {
		render(NeighborhoodGraph, { graph });
		const nodes = screen.getAllByRole('button');
		expect(nodes).toHaveLength(2);
		// Node labels render the concept code; the full label is the <title>.
		expect(screen.getByText('C3262')).toBeInTheDocument();
		expect(screen.getByText('C9305')).toBeInTheDocument();
	});

	it('renders the edge relation label', () => {
		render(NeighborhoodGraph, { graph });
		expect(screen.getByText('is a')).toBeInTheDocument();
	});

	it('navigates to the concept page when a node is clicked', async () => {
		render(NeighborhoodGraph, { graph });
		await fireEvent.click(screen.getAllByRole('button')[0]);
		await tick();
		expect(goto).toHaveBeenCalledWith('/repositories/ncit/C3262');
	});

	it('navigates on Enter but ignores other keys', async () => {
		render(NeighborhoodGraph, { graph });
		const node = screen.getAllByRole('button')[1]; // C9305
		await fireEvent.keyDown(node, { key: 'a' });
		expect(goto).not.toHaveBeenCalled();
		await fireEvent.keyDown(node, { key: 'Enter' });
		expect(goto).toHaveBeenCalledWith('/repositories/ncit/C9305');
	});

	it('does not draw an edge with an endpoint missing from the node set', () => {
		render(NeighborhoodGraph, {
			graph: {
				center: 'C3262',
				nodes: [{ code: 'C3262', label: 'Neoplasm', semantic_type: null }],
				edges: [
					{
						source: 'C3262',
						target: 'GHOST',
						relation: 'subClassOf',
						relation_label: 'is a',
						kind: 'subClassOf'
					}
				]
			}
		});
		// The dangling edge (target has no position) is not rendered.
		expect(screen.queryByText('is a')).not.toBeInTheDocument();
	});

	it('falls back to the relation code when an edge has no label', () => {
		render(NeighborhoodGraph, {
			graph: {
				center: 'C3262',
				nodes: [
					{ code: 'C3262', label: 'Neoplasm', semantic_type: null },
					{ code: 'C9305', label: null, semantic_type: null }
				],
				edges: [
					{
						source: 'C9305',
						target: 'C3262',
						relation: 'R99',
						relation_label: null,
						kind: 'role'
					}
				]
			}
		});
		expect(screen.getByText('R99')).toBeInTheDocument();
	});
});
