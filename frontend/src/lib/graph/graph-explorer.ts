/**
 * Pure, WebGL-free logic for the interactive graph explorer.
 *
 * The `GraphExplorer.svelte` / `GraphMinimap.svelte` components are thin imperative
 * shells around sigma (WebGL) and a 2d canvas — untestable in jsdom and covered by
 * the Playwright e2e flows. Everything that can be decided without a renderer lives
 * here so it gets real, fast unit tests: color assignment, layout seeding, node/edge
 * appearance rules (the sigma reducers), node search, and the minimap projection.
 */
import type Graph from 'graphology';
import {
	communityColor,
	seededNodePosition,
	type NodeAttrs
} from '$lib/graph/neighborhood-graph';

export type ColorMode = 'community' | 'semantic';
export type GraphTheme = 'light' | 'dark';

export interface GraphLabelColors {
	canvasColor: string;
	hoverBackgroundColor: string;
	labelColor: string;
	edgeLabelColor: string;
}

export interface GraphLabelSettings {
	labelDensity: number;
	labelGridCellSize: number;
	labelRenderedSizeThreshold: number;
}

export interface GraphLabelRenderer {
	setSettings(settings: {
		labelColor: { color: string };
		edgeLabelColor: { color: string };
	}): unknown;
	refresh(): unknown;
}

export interface GraphLabelPolicyRenderer {
	getSetting(key: keyof GraphLabelSettings): number;
	setSettings(settings: GraphLabelSettings): unknown;
	refresh(): unknown;
}

export interface GraphLabelBounds {
	left: number;
	top: number;
	right: number;
	bottom: number;
}

export interface ForceAtlasLayoutBudget {
	durationMs: number;
}

export interface LayoutWorker {
	start(): void;
	stop(): void;
	kill(): void;
}

export interface LayoutTimers<Handle> {
	schedule(callback: () => void, delayMs: number): Handle;
	clear(handle: Handle): void;
}

/** Fallback color for a node with no semantic type. */
const NO_TYPE_COLOR = '#94a3b8';
/** Color applied to nodes/labels dimmed while another node is hovered. */
const DIMMED_COLOR = '#cbd5e1';

const GRAPH_LABEL_THEMES: Record<GraphTheme, GraphLabelColors> = {
	light: {
		canvasColor: '#e8f4fc',
		hoverBackgroundColor: '#ffffff',
		labelColor: '#0d2140',
		edgeLabelColor: '#404040'
	},
	dark: {
		canvasColor: '#171717',
		hoverBackgroundColor: '#262626',
		labelColor: '#fafafa',
		edgeLabelColor: '#d4d4d4'
	}
};

const GRAPH_LABEL_MAX_CHARACTERS = 32;

/** Canvas and label colors whose contrast is verified by the unit contract. */
export function graphLabelTheme(theme: GraphTheme): GraphLabelColors {
	return GRAPH_LABEL_THEMES[theme];
}

/** Update label colors without replacing the renderer, graph, or camera. */
export function applyGraphLabelTheme(renderer: GraphLabelRenderer, theme: GraphTheme): void {
	const colors = graphLabelTheme(theme);
	renderer.setSettings({
		labelColor: { color: colors.labelColor },
		edgeLabelColor: { color: colors.edgeLabelColor }
	});
	renderer.refresh();
}

/** Keep one collision-grid candidate per cell at default zoom and reveal more when closer. */
export function graphLabelPolicy(cameraRatio: number): GraphLabelSettings {
	const zoomedIn = cameraRatio < 0.6;
	return {
		labelDensity: zoomedIn ? 0.5 : 0.45,
		labelGridCellSize: 240,
		labelRenderedSizeThreshold: zoomedIn ? 3 : 9
	};
}

/** Apply a changed zoom bucket and immediately rebuild Sigma's program indices. */
export function applyGraphLabelPolicy(
	renderer: GraphLabelPolicyRenderer,
	cameraRatio: number
): void {
	const policy = graphLabelPolicy(cameraRatio);
	const changed = (Object.keys(policy) as Array<keyof GraphLabelSettings>).some(
		(key) => renderer.getSetting(key) !== policy[key]
	);
	if (!changed) return;

	// Sigma's setSettings clears program indices before its scheduled render. A layout
	// worker can emit a partial repaint in that gap, so rebuild synchronously here.
	renderer.setSettings(policy);
	renderer.refresh();
}

/** Bound canvas label width while leaving the graph's source label untouched. */
export function ellipsizeGraphLabel(
	label: string,
	maxCharacters = GRAPH_LABEL_MAX_CHARACTERS
): string {
	if (label.length <= maxCharacters) return label;
	return `${label.slice(0, maxCharacters)}…`;
}

/** Axis-aligned bounds matching Sigma's disc-label baseline, with breathing room. */
export function graphLabelBounds(input: {
	x: number;
	y: number;
	nodeSize: number;
	labelSize: number;
	labelWidth: number;
}): GraphLabelBounds {
	const left = input.x + input.nodeSize + 3;
	const baseline = input.y + input.labelSize / 3;
	return {
		left: left - 2,
		top: baseline - input.labelSize - 2,
		right: left + input.labelWidth + 2,
		bottom: baseline + 2
	};
}

/** Per-render collision guard shared by node and edge canvas labels. */
export class GraphLabelCollisionIndex {
	private bounds: GraphLabelBounds[] = [];

	claim(candidate: GraphLabelBounds): boolean {
		const overlaps = this.bounds.some(
			(existing) =>
				candidate.left < existing.right &&
				candidate.right > existing.left &&
				candidate.top < existing.bottom &&
				candidate.bottom > existing.top
		);
		if (overlaps) return false;
		this.bounds.push(candidate);
		return true;
	}

	reset(): void {
		this.bounds = [];
	}
}

const SEMANTIC_PALETTE = [
	'#007bbd',
	'#e85a7a',
	'#298085',
	'#c98f00',
	'#7c5cbf',
	'#1a8fcf',
	'#d1495b',
	'#4c956c'
];

/**
 * Return a bounded wall-clock budget for the worker layout.
 *
 * Larger and denser graphs get more settling time, while the hard cap keeps a
 * replacement or navigation from retaining a worker indefinitely.
 */
export function forceAtlasLayoutBudget(nodeCount: number, edgeCount: number): ForceAtlasLayoutBudget {
	const nodes = Math.max(0, Math.floor(nodeCount));
	const edges = Math.max(0, Math.floor(edgeCount));
	const boundedEdges = Math.min(edges, nodes * 4);
	return {
		durationMs: Math.min(1_500, Math.max(250, Math.round(250 + nodes * 2 + boundedEdges / 2)))
	};
}

const BROWSER_LAYOUT_TIMERS: LayoutTimers<ReturnType<typeof setTimeout>> = {
	schedule: (callback, delayMs) => setTimeout(callback, delayMs),
	clear: (handle) => clearTimeout(handle)
};

/** Own one timed worker run and make replacement/destruction cancellation explicit. */
export class TimedLayoutController<Handle = ReturnType<typeof setTimeout>> {
	private worker: LayoutWorker | null = null;
	private timer: Handle | null = null;
	private generation = 0;

	constructor(
		private readonly timers: LayoutTimers<Handle> = BROWSER_LAYOUT_TIMERS as LayoutTimers<Handle>
	) {}

	get running(): boolean {
		return this.worker !== null;
	}

	start(worker: LayoutWorker, durationMs: number, onSettled: () => void): void {
		this.cancel();
		const generation = this.generation;
		this.worker = worker;
		try {
			worker.start();
		} catch (error) {
			this.worker = null;
			worker.kill();
			throw error;
		}
		try {
			this.timer = this.timers.schedule(() => {
				if (generation !== this.generation || worker !== this.worker) return;
				this.worker = null;
				this.timer = null;
				this.terminate(worker);
				onSettled();
			}, durationMs);
		} catch (error) {
			this.worker = null;
			this.timer = null;
			this.terminate(worker);
			throw error;
		}
	}

	cancel(): void {
		this.generation += 1;
		if (this.timer !== null) this.timers.clear(this.timer);
		this.timer = null;
		const worker = this.worker;
		this.worker = null;
		if (worker) this.terminate(worker);
	}

	private terminate(worker: LayoutWorker): void {
		try {
			worker.stop();
		} finally {
			worker.kill();
		}
	}
}

/**
 * A stateful semantic-type → color mapper: colors are assigned from the palette in
 * first-seen order and cached, so a given type keeps its color for the session and
 * the palette cycles once exhausted.
 */
export function makeSemanticColorer(
	palette: string[] = SEMANTIC_PALETTE
): (type: string | null) => string {
	const cache = new Map<string, string>();
	return (type: string | null): string => {
		if (!type) return NO_TYPE_COLOR;
		let color = cache.get(type);
		if (color === undefined) {
			color = palette[cache.size % palette.length];
			cache.set(type, color);
		}
		return color;
	};
}

/** Resolve a node's color for the active color mode. */
export function nodeColorForMode(
	mode: ColorMode,
	attrs: NodeAttrs,
	semanticColorer: (type: string | null) => string
): string {
	return mode === 'community'
		? communityColor(attrs.community)
		: semanticColorer(attrs.semanticType);
}

/** Give every coordinate-less node an identity-stable position for layout startup. */
export function seedPositions(graph: Graph): void {
	graph.forEachNode((n, attrs) => {
		if (typeof attrs.x !== 'number' || typeof attrs.y !== 'number') {
			const position = seededNodePosition(n);
			graph.setNodeAttribute(n, 'x', position.x);
			graph.setNodeAttribute(n, 'y', position.y);
		}
	});
}

/** Replace any non-finite coordinate with a finite one (sigma throws on NaN/Infinity). */
export function ensureFinite(graph: Graph): void {
	graph.forEachNode((n, attrs) => {
		if (!Number.isFinite(attrs.x as number) || !Number.isFinite(attrs.y as number)) {
			const position = seededNodePosition(n);
			graph.setNodeAttribute(n, 'x', position.x);
			graph.setNodeAttribute(n, 'y', position.y);
		}
	});
}

/** Distinct semantic types present in the graph, sorted for a stable filter list. */
export function collectSemanticTypes(graph: Graph): string[] {
	const types = new Set<string>();
	graph.forEachNode((_n, attrs) => {
		const t = (attrs as NodeAttrs).semanticType;
		if (t) types.add(t);
	});
	return [...types].sort();
}

/** The node itself plus its immediate neighbors — the "keep visible on hover" set. */
export function neighborSet(graph: Graph, node: string): Set<string> {
	const set = new Set<string>([node]);
	graph.forEachNeighbor(node, (n) => set.add(n));
	return set;
}

/**
 * Find the first node matching a free-text term: an exact (case-insensitive) code
 * match or a label substring match. Returns the node code, or null if none match.
 */
export function findNode(graph: Graph, term: string): string | null {
	const needle = term.trim().toLowerCase();
	if (!needle) return null;
	let found: string | null = null;
	graph.forEachNode((n, attrs) => {
		if (found) return;
		const label = ((attrs as NodeAttrs).label ?? '').toLowerCase();
		if (n.toLowerCase() === needle || label.includes(needle)) found = n;
	});
	return found;
}

export interface NodeAppearance {
	hidden?: boolean;
	highlighted?: boolean;
	color?: string;
	label?: string;
	type?: string;
}

/**
 * Decide a node's sigma appearance overrides. Mirrors the interactive rules: the
 * center is always shown (and drawn as a circle); nodes of a hidden semantic type or
 * isolated (when that filter is on) are hidden — except the center; the selected node
 * is highlighted; and while hovering, non-adjacent nodes are dimmed and de-labelled.
 */
export function reduceNodeAppearance(opts: {
	node: string;
	centerCode: string;
	semanticType: string | null;
	degree: number;
	hiddenTypes: ReadonlySet<string>;
	hideIsolated: boolean;
	selectedCode: string | null;
	hovered: string | null;
	hoveredNeighbors: ReadonlySet<string> | null;
}): NodeAppearance {
	const res: NodeAppearance = {};
	const isCenter = opts.node === opts.centerCode;
	if (isCenter) res.type = 'circle';
	if (!isCenter) {
		if (opts.semanticType && opts.hiddenTypes.has(opts.semanticType)) res.hidden = true;
		if (opts.hideIsolated && opts.degree === 0) res.hidden = true;
	}
	if (opts.selectedCode && opts.node === opts.selectedCode) res.highlighted = true;
	if (opts.hovered && opts.hoveredNeighbors && !opts.hoveredNeighbors.has(opts.node)) {
		res.color = DIMMED_COLOR;
		res.label = '';
	}
	return res;
}

/** Hide an edge while hovering unless it is incident to the hovered node. */
export function reduceEdgeAppearance(opts: {
	hovered: string | null;
	source: string;
	target: string;
}): { hidden?: boolean } {
	if (opts.hovered && opts.source !== opts.hovered && opts.target !== opts.hovered) {
		return { hidden: true };
	}
	return {};
}

// --- minimap projection (GraphMinimap.svelte's pure geometry) --------------------

export interface MinimapBounds {
	minX: number;
	minY: number;
	spanX: number;
	spanY: number;
}

/** Bounding box of all node positions; null for an empty graph. Zero spans clamp to 1. */
export function minimapBounds(graph: Graph): MinimapBounds | null {
	if (graph.order === 0) return null;
	let minX = Infinity;
	let minY = Infinity;
	let maxX = -Infinity;
	let maxY = -Infinity;
	graph.forEachNode((_n, attrs) => {
		const x = attrs.x as number;
		const y = attrs.y as number;
		if (x < minX) minX = x;
		if (y < minY) minY = y;
		if (x > maxX) maxX = x;
		if (y > maxY) maxY = y;
	});
	return { minX, minY, spanX: maxX - minX || 1, spanY: maxY - minY || 1 };
}

/** Project a graph coordinate into minimap canvas space (y flipped: canvas grows down). */
export function projectToMinimap(
	gx: number,
	gy: number,
	b: MinimapBounds,
	dims: { width: number; height: number; pad: number }
): { x: number; y: number } {
	return {
		x: dims.pad + ((gx - b.minX) / b.spanX) * (dims.width - 2 * dims.pad),
		y: dims.pad + (1 - (gy - b.minY) / b.spanY) * (dims.height - 2 * dims.pad)
	};
}
