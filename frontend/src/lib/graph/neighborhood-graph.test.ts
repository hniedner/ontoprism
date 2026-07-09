import { describe, it, expect } from 'vitest';
import type { Neighborhood } from '$lib/types';
import {
	createGraph,
	mergeNeighborhood,
	assignAnalytics,
	degreeToSize,
	communityColor
} from './neighborhood-graph';

function nb(partial: Partial<Neighborhood> & { center: string }): Neighborhood {
	return { nodes: [], edges: [], ...partial };
}

describe('mergeNeighborhood', () => {
	it('does not overwrite a label when the merged node has no label', () => {
		const g = createGraph();
		mergeNeighborhood(g, nb({ center: 'C1', nodes: [{ code: 'C1', label: 'Original', semantic_type: null }] }));
		mergeNeighborhood(
			g,
			nb({
				center: 'C2',
				nodes: [
					{ code: 'C1', label: null, semantic_type: null },
					{ code: 'C2', label: 'New', semantic_type: null }
				]
			})
		);
		expect(g.getNodeAttribute('C1', 'label')).toBe('Original');
	});

	it('adds center and neighbor nodes with edges', () => {
		const g = createGraph();
		const added = mergeNeighborhood(
			g,
			nb({
				center: 'C1',
				nodes: [
					{ code: 'C1', label: 'Root', semantic_type: 'Neoplastic Process' },
					{ code: 'C2', label: 'Child', semantic_type: null }
				],
				edges: [{ source: 'C2', target: 'C1', relation: 'R1', relation_label: 'is_a', kind: 'subClassOf' }]
			})
		);
		expect(added).toBe(2);
		expect(g.order).toBe(2);
		expect(g.size).toBe(1);
		expect(g.getNodeAttribute('C1', 'label')).toBe('Root');
		expect(g.getNodeAttribute('C1', 'expanded')).toBe(true);
		expect(g.getNodeAttribute('C2', 'expanded')).toBe(false);
	});

	it('deduplicates nodes and edges across merges and fills in labels', () => {
		const g = createGraph();
		mergeNeighborhood(g, nb({ center: 'C1', nodes: [{ code: 'C1', label: 'Root', semantic_type: null }] }));
		mergeNeighborhood(
			g,
			nb({
				center: 'C2',
				nodes: [
					{ code: 'C2', label: 'Real Label', semantic_type: 'X' },
					{ code: 'C1', label: 'Root', semantic_type: null }
				],
				edges: [{ source: 'C1', target: 'C2', relation: 'R2', relation_label: 'assoc', kind: 'association' }]
			})
		);
		mergeNeighborhood(
			g,
			nb({
				center: 'C2',
				nodes: [{ code: 'C2', label: 'Real Label', semantic_type: 'X' }],
				edges: [{ source: 'C1', target: 'C2', relation: 'R2', relation_label: 'assoc', kind: 'association' }]
			})
		);
		expect(g.order).toBe(2);
		expect(g.size).toBe(1);
		expect(g.getNodeAttribute('C2', 'label')).toBe('Real Label');
	});

	it('skips edges whose endpoints are not both present', () => {
		const g = createGraph();
		mergeNeighborhood(
			g,
			nb({
				center: 'C1',
				nodes: [{ code: 'C1', label: 'Root', semantic_type: null }],
				edges: [{ source: 'C1', target: 'MISSING', relation: 'R', relation_label: null, kind: 'role' }]
			})
		);
		expect(g.size).toBe(0);
	});

	it('gives every node a finite starting position so sigma never throws', () => {
		const g = createGraph();
		mergeNeighborhood(
			g,
			nb({
				center: 'C1',
				nodes: [
					{ code: 'C1', label: 'A', semantic_type: null },
					{ code: 'C2', label: 'B', semantic_type: null }
				]
			})
		);
		g.forEachNode((_n, attrs) => {
			expect(Number.isFinite(attrs.x as number)).toBe(true);
			expect(Number.isFinite(attrs.y as number)).toBe(true);
		});
	});

	it('falls back to the code as label for a node with no label', () => {
		const g = createGraph();
		mergeNeighborhood(g, nb({ center: 'C1', nodes: [{ code: 'C1', label: null, semantic_type: null }] }));
		expect(g.getNodeAttribute('C1', 'label')).toBe('C1');
	});

	it('does not mark the center expanded when centerExpanded is false', () => {
		const g = createGraph();
		mergeNeighborhood(
			g,
			nb({ center: 'C1', nodes: [{ code: 'C1', label: 'Root', semantic_type: null }] }),
			{ centerExpanded: false }
		);
		expect(g.getNodeAttribute('C1', 'expanded')).toBe(false);
	});

	it('uses the bare relation code as the edge label when no relation_label is given', () => {
		const g = createGraph();
		mergeNeighborhood(
			g,
			nb({
				center: 'C1',
				nodes: [
					{ code: 'C1', label: 'A', semantic_type: null },
					{ code: 'C2', label: 'B', semantic_type: null }
				],
				edges: [{ source: 'C1', target: 'C2', relation: 'R42', relation_label: null, kind: 'role' }]
			})
		);
		expect(g.getEdgeAttribute('C1|R42|C2', 'label')).toBe('R42');
	});

	it('does not mark the center expanded when it is absent from the graph', () => {
		const g = createGraph();
		g.addNode('X', { code: 'X', label: 'Only' });
		const added = mergeNeighborhood(g, nb({ center: 'CENTER', nodes: [] }));
		expect(added).toBe(0);
		expect(g.getNodeAttribute('X', 'expanded')).toBe(undefined);
	});
});

describe('assignAnalytics', () => {
	it('is safe on an empty graph', () => {
		const summary = assignAnalytics(createGraph());
		expect(summary).toEqual({ communityCount: 0, topByDegree: [], topByBetweenness: [] });
	});

	it('zeroes community/betweenness on a graph with nodes but no edges', () => {
		const g = createGraph();
		mergeNeighborhood(
			g,
			nb({
				center: 'C1',
				nodes: [
					{ code: 'C1', label: 'A', semantic_type: null },
					{ code: 'C2', label: 'B', semantic_type: null }
				]
			})
		);
		const summary = assignAnalytics(g);
		expect(g.getNodeAttribute('C1', 'community')).toBe(0);
		expect(g.getNodeAttribute('C1', 'betweenness')).toBe(0);
		expect(summary.communityCount).toBe(1);
		expect(summary.topByBetweenness[0].betweenness).toBe(0);
	});

	it('assigns degree and ranks the most connected node first', () => {
		const g = createGraph();
		mergeNeighborhood(
			g,
			nb({
				center: 'HUB',
				nodes: [
					{ code: 'HUB', label: 'Hub', semantic_type: null },
					{ code: 'A', label: 'A', semantic_type: null },
					{ code: 'B', label: 'B', semantic_type: null }
				],
				edges: [
					{ source: 'A', target: 'HUB', relation: 'r', relation_label: null, kind: 'role' },
					{ source: 'HUB', target: 'B', relation: 'r', relation_label: null, kind: 'role' }
				]
			})
		);
		const summary = assignAnalytics(g);
		expect(summary.topByDegree[0].code).toBe('HUB');
		expect(g.getNodeAttribute('HUB', 'degree')).toBe(2);
		expect(summary.communityCount).toBeGreaterThanOrEqual(1);
		expect(summary.topByBetweenness[0].code).toBe('HUB');
		expect(g.getNodeAttribute('HUB', 'betweenness')).toBeGreaterThan(0);
	});

	it('covers the ?? fallback in assignAnalytics when a node lacks community/label/betweenness', () => {
		const g = createGraph();
		g.addNode('N1', { code: 'N1' });
		g.addNode('N2', { code: 'N2' });
		g.addEdgeWithKey('e1', 'N1', 'N2', { label: 'r', kind: 'role', color: '#aaa' });
		const summary = assignAnalytics(g);
		expect(summary.communityCount).toBeGreaterThanOrEqual(1);
		expect(g.getNodeAttribute('N1', 'community')).toBeGreaterThanOrEqual(0);
		expect(g.getNodeAttribute('N1', 'degree')).toBe(1);
	});
});

describe('scales', () => {
	it('degreeToSize grows with degree and is clamped', () => {
		expect(degreeToSize(0)).toBeLessThan(degreeToSize(4));
		expect(degreeToSize(10_000)).toBeLessThanOrEqual(22);
	});

	it('communityColor is a stable hex color, wraps, and falls back for undefined', () => {
		expect(communityColor(0)).toMatch(/^#[0-9a-f]{6}$/i);
		expect(communityColor(0)).not.toBe(communityColor(1));
		expect(communityColor(undefined)).toBe(communityColor(0));
		expect(communityColor(0)).toBe(communityColor(10));
	});
});
