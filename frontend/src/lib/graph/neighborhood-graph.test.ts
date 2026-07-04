import { describe, it, expect } from 'vitest';
import type { Neighborhood } from '$lib/types';
import {
	createGraph,
	mergeNeighborhood,
	assignAnalytics,
	degreeToSize,
	communityColor,
	COMMUNITY_PALETTE
} from './neighborhood-graph';

function nb(partial: Partial<Neighborhood> & { center: string }): Neighborhood {
	return { nodes: [], edges: [], ...partial };
}

describe('mergeNeighborhood', () => {
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
		// C2 first appears only as a bare reference, then arrives with a real label.
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
		// Re-merging the same payload must not create duplicate nodes or edges.
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
});

describe('assignAnalytics', () => {
	it('is safe on an empty graph', () => {
		const summary = assignAnalytics(createGraph());
		expect(summary).toEqual({ communityCount: 0, topByDegree: [] });
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
					{ source: 'B', target: 'HUB', relation: 'r', relation_label: null, kind: 'role' }
				]
			})
		);
		const summary = assignAnalytics(g);
		expect(summary.topByDegree[0].code).toBe('HUB');
		expect(g.getNodeAttribute('HUB', 'degree')).toBe(2);
		expect(summary.communityCount).toBeGreaterThanOrEqual(1);
	});
});

describe('scales', () => {
	it('degreeToSize grows with degree and is clamped', () => {
		expect(degreeToSize(0)).toBeLessThan(degreeToSize(4));
		expect(degreeToSize(10_000)).toBeLessThanOrEqual(22);
	});

	it('communityColor cycles the palette and is stable', () => {
		expect(communityColor(0)).toBe(COMMUNITY_PALETTE[0]);
		expect(communityColor(COMMUNITY_PALETTE.length)).toBe(COMMUNITY_PALETTE[0]);
		expect(communityColor(undefined)).toBe(COMMUNITY_PALETTE[0]);
	});
});
