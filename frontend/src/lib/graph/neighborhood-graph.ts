/**
 * Pure, WebGL-free helpers for the interactive NCIt graph explorer.
 *
 * Builds and grows a graphology graph from the backend `Neighborhood` payloads
 * (expand-on-demand), and derives network-analysis attributes — Louvain
 * communities and degree centrality — used for node coloring and sizing.
 *
 * Kept free of `sigma` so it can be unit-tested in a plain node environment.
 */
import Graph from 'graphology';
import louvain from 'graphology-communities-louvain';
import betweenness from 'graphology-metrics/centrality/betweenness';
import type { EdgeKind, Neighborhood } from '$lib/types';

/** Edge color by ontological kind — reused by the legend. */
export const KIND_COLOR: Record<EdgeKind, string> = {
	subClassOf: '#94a3b8',
	role: '#3b9de0',
	association: '#42979a',
	'cde-concept': '#e0a53b' // amber: the caDSR CDE → NCIt concept join edge
};

/** Distinct, theme-neutral palette cycled across detected communities. */
const COMMUNITY_PALETTE = [
	'#007bbd',
	'#e85a7a',
	'#298085',
	'#c98f00',
	'#7c5cbf',
	'#1a8fcf',
	'#d1495b',
	'#4c956c',
	'#e07a5f',
	'#3d5a80'
];

export interface NodeAttrs {
	label: string;
	code: string;
	semanticType: string | null;
	/** Present after `assignAnalytics`. */
	community?: number;
	degree?: number;
	betweenness?: number;
	/** Whether this node's own neighborhood has been fetched and merged. */
	expanded?: boolean;
	[key: string]: unknown;
}

/** A fresh directed multigraph (parallel edges carry distinct relations). */
export function createGraph(): Graph {
	return new Graph({ multi: true, type: 'directed', allowSelfLoops: false });
}

/**
 * Merge a `Neighborhood` into `graph`, deduplicating nodes and edges.
 * The `centerExpanded` flag marks the center node as fully expanded so the UI
 * can avoid re-fetching it. Returns the count of newly added nodes.
 */
export function mergeNeighborhood(
	graph: Graph,
	nb: Neighborhood,
	{ centerExpanded = true }: { centerExpanded?: boolean } = {}
): number {
	let added = 0;
	for (const node of nb.nodes) {
		if (graph.hasNode(node.code)) {
			// A previously-referenced neighbor now arrives with real attributes.
			if (node.label) graph.setNodeAttribute(node.code, 'label', node.label);
			if (node.semantic_type)
				graph.setNodeAttribute(node.code, 'semanticType', node.semantic_type);
		} else {
			// Seed a position at creation so sigma never renders a coordinate-less
			// node (it throws); the force layout overwrites these immediately after.
			const angle = Math.random() * 2 * Math.PI;
			const r = 0.5 + Math.random();
			graph.addNode(node.code, {
				label: node.label ?? node.code,
				code: node.code,
				semanticType: node.semantic_type,
				expanded: false,
				x: Math.cos(angle) * r,
				y: Math.sin(angle) * r
			} satisfies NodeAttrs);
			added += 1;
		}
	}
	if (centerExpanded && graph.hasNode(nb.center)) {
		graph.setNodeAttribute(nb.center, 'expanded', true);
	}
	for (const edge of nb.edges) {
		if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
		const key = `${edge.source}|${edge.relation}|${edge.target}`;
		if (graph.hasEdge(key)) continue;
		graph.addEdgeWithKey(key, edge.source, edge.target, {
			label: edge.relation_label ?? edge.relation,
			kind: edge.kind,
			color: KIND_COLOR[edge.kind]
		});
	}
	return added;
}

export interface AnalyticsSummary {
	communityCount: number;
	/** Node codes sorted by descending degree (most connected first). */
	topByDegree: { code: string; label: string; degree: number }[];
	/** Node codes sorted by descending betweenness (key bridging concepts first). */
	topByBetweenness: { code: string; label: string; betweenness: number }[];
}

/** Assign community + betweenness to every node (or zero them on an edgeless graph). */
function assignCentrality(graph: Graph): void {
	if (graph.size > 0) {
		louvain.assign(graph, { nodeCommunityAttribute: 'community' });
		// Betweenness = how often a node lies on shortest paths — the bridging concepts.
		// Cheap here (neighborhoods are bounded to a few hundred nodes).
		betweenness.assign(graph, { nodeCentralityAttribute: 'betweenness', normalized: true });
		return;
	}
	graph.forEachNode((n) => {
		graph.setNodeAttribute(n, 'community', 0);
		graph.setNodeAttribute(n, 'betweenness', 0);
	});
}

/**
 * Assign `community` (Louvain), `degree`, and `betweenness` attributes to every node
 * and return a summary for the stats panel. Safe on an empty graph.
 */
export function assignAnalytics(graph: Graph, { topN = 5 }: { topN?: number } = {}): AnalyticsSummary {
	if (graph.order === 0) return { communityCount: 0, topByDegree: [], topByBetweenness: [] };

	assignCentrality(graph);

	const communities = new Set<number>();
	const degrees: { code: string; label: string; degree: number }[] = [];
	const between: { code: string; label: string; betweenness: number }[] = [];
	graph.forEachNode((n, attrs) => {
		const degree = graph.degree(n);
		graph.setNodeAttribute(n, 'degree', degree);
		communities.add((attrs.community as number) ?? 0);
		const label = (attrs.label as string) ?? n;
		degrees.push({ code: n, label, degree });
		between.push({ code: n, label, betweenness: (attrs.betweenness as number) ?? 0 });
	});
	degrees.sort((a, b) => b.degree - a.degree);
	between.sort((a, b) => b.betweenness - a.betweenness);

	return {
		communityCount: communities.size,
		topByDegree: degrees.slice(0, topN),
		topByBetweenness: between.slice(0, topN)
	};
}

/** Node radius from degree centrality (clamped for readability). */
export function degreeToSize(degree: number): number {
	return Math.min(22, 6 + Math.sqrt(degree) * 3);
}

/** Stable color for a community index. */
export function communityColor(community: number | undefined): string {
	if (community === undefined) return COMMUNITY_PALETTE[0];
	return COMMUNITY_PALETTE[community % COMMUNITY_PALETTE.length];
}
