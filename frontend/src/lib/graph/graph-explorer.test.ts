import { describe, expect, it } from 'vitest';
import Graph from 'graphology';
import * as graphExplorer from './graph-explorer';
import {
	makeSemanticColorer,
	nodeColorForMode,
	seedPositions,
	ensureFinite,
	collectSemanticTypes,
	neighborSet,
	findNode,
	reduceNodeAppearance,
	reduceEdgeAppearance,
	minimapBounds,
	projectToMinimap
} from './graph-explorer';
import { communityColor, type NodeAttrs } from './neighborhood-graph';

// Colors the module keeps private; asserted here by their documented literal values.
const NO_TYPE_COLOR = '#94a3b8';
const DIMMED_COLOR = '#cbd5e1';

function graphWith(nodes: Array<[string, Partial<NodeAttrs>]>): Graph {
	const g = new Graph({ multi: true, type: 'directed', allowSelfLoops: false });
	for (const [code, attrs] of nodes) g.addNode(code, { code, label: code, ...attrs });
	return g;
}

describe('makeSemanticColorer', () => {
	it('assigns palette colors in first-seen order and caches them', () => {
		const color = makeSemanticColorer(['#a', '#b', '#c']);
		expect(color('Disease')).toBe('#a');
		expect(color('Gene')).toBe('#b');
		// Re-querying a known type returns the same cached color, not the next palette slot.
		expect(color('Disease')).toBe('#a');
	});

	it('cycles the palette once exhausted', () => {
		const color = makeSemanticColorer(['#a', '#b']);
		color('one');
		color('two');
		expect(color('three')).toBe('#a');
	});

	it('returns the neutral color for a null type', () => {
		expect(makeSemanticColorer()('')).toBe(NO_TYPE_COLOR);
		expect(makeSemanticColorer()(null)).toBe(NO_TYPE_COLOR);
	});
});

describe('nodeColorForMode', () => {
	it('uses the community palette in community mode', () => {
		const attrs = { code: 'C1', label: 'C1', community: 2, semanticType: 'Gene' } as NodeAttrs;
		expect(nodeColorForMode('community', attrs, makeSemanticColorer())).toBe(communityColor(2));
	});

	it('uses the semantic colorer in semantic mode', () => {
		const colorer = makeSemanticColorer(['#zzz']);
		const attrs = { code: 'C1', label: 'C1', community: 2, semanticType: 'Gene' } as NodeAttrs;
		expect(nodeColorForMode('semantic', attrs, colorer)).toBe('#zzz');
	});
});

describe('seedPositions', () => {
	it('assigns finite coordinates only to nodes that lack them', () => {
		const g = graphWith([
			['A', { x: 5, y: 7, fixed: true }],
			['B', {}]
		]);
		seedPositions(g);
		// Existing coordinates are preserved.
		expect(g.getNodeAttribute('A', 'x')).toBe(5);
		expect(g.getNodeAttribute('A', 'y')).toBe(7);
		expect(g.getNodeAttribute('A', 'fixed')).toBe(true);
		// The coordinate-less node now has finite positions.
		expect(Number.isFinite(g.getNodeAttribute('B', 'x') as number)).toBe(true);
		expect(Number.isFinite(g.getNodeAttribute('B', 'y') as number)).toBe(true);
	});

	it('assigns stable positions from node identity rather than random state or insertion order', () => {
		const first = graphWith([
			['A', {}],
			['B', {}],
			['C', {}]
		]);
		const reordered = graphWith([
			['C', {}],
			['A', {}],
			['B', {}]
		]);

		seedPositions(first);
		seedPositions(reordered);

		for (const node of ['A', 'B', 'C']) {
			expect(reordered.getNodeAttribute(node, 'x')).toBe(first.getNodeAttribute(node, 'x'));
			expect(reordered.getNodeAttribute(node, 'y')).toBe(first.getNodeAttribute(node, 'y'));
		}
	});
});

describe('ensureFinite', () => {
	it('replaces NaN/Infinity coordinates with finite ones', () => {
		const g = graphWith([
			['A', { x: NaN, y: 3 }],
			['B', { x: Infinity, y: Infinity }],
			['C', { x: 1, y: 2 }]
		]);
		ensureFinite(g);
		for (const n of ['A', 'B', 'C']) {
			expect(Number.isFinite(g.getNodeAttribute(n, 'x') as number)).toBe(true);
			expect(Number.isFinite(g.getNodeAttribute(n, 'y') as number)).toBe(true);
		}
		// A finite node is untouched.
		expect(g.getNodeAttribute('C', 'x')).toBe(1);
	});
});

describe('forceAtlasLayoutBudget', () => {
	it('adapts a bounded wall-clock budget to graph size and density', () => {
		const candidate = (graphExplorer as unknown as Record<string, unknown>)[
			'forceAtlasLayoutBudget'
		];
		expect(candidate).toBeTypeOf('function');
		const budget = candidate as (nodes: number, edges: number) => { durationMs: number };

		const small = budget(20, 19);
		const reported = budget(186, 191);
		const dense = budget(186, 600);
		const nearCap = budget(400, 800);

		expect(small.durationMs).toBeLessThan(reported.durationMs);
		expect(dense.durationMs).toBeGreaterThan(reported.durationMs);
		expect(nearCap.durationMs).toBeGreaterThan(dense.durationMs);
		expect(small.durationMs).toBeGreaterThanOrEqual(250);
		expect(nearCap.durationMs).toBeLessThanOrEqual(1_500);
		expect(budget(186, 191)).toEqual(reported);
	});
});

describe('TimedLayoutController', () => {
	it('kills replaced workers and ignores stale timers before settling the current run', () => {
		interface WorkerDouble {
			events: string[];
			start(): void;
			stop(): void;
			kill(): void;
		}
		interface Controller {
			start(worker: WorkerDouble, durationMs: number, onSettled: () => void): void;
			cancel(): void;
			readonly running: boolean;
		}
		type ControllerConstructor = new (timers: {
			schedule: (callback: () => void, delayMs: number) => number;
			clear: (handle: number) => void;
		}) => Controller;

		const candidate = (graphExplorer as unknown as Record<string, unknown>)[
			'TimedLayoutController'
		];
		expect(candidate).toBeTypeOf('function');
		const Controller = candidate as ControllerConstructor;
		const scheduled: Array<{ callback: () => void; delayMs: number }> = [];
		const cleared: number[] = [];
		const controller = new Controller({
			schedule: (callback, delayMs) => {
				scheduled.push({ callback, delayMs });
				return scheduled.length;
			},
			clear: (handle) => cleared.push(handle)
		});
		const worker = (): WorkerDouble => ({
			events: [],
			start() {
				this.events.push('start');
			},
			stop() {
				this.events.push('stop');
			},
			kill() {
				this.events.push('kill');
			}
		});
		const first = worker();
		const second = worker();
		const settled: string[] = [];

		controller.start(first, 300, () => settled.push('first'));
		controller.start(second, 600, () => settled.push('second'));

		expect(first.events).toEqual(['start', 'stop', 'kill']);
		expect(cleared).toEqual([1]);
		expect(scheduled.map(({ delayMs }) => delayMs)).toEqual([300, 600]);
		expect(controller.running).toBe(true);

		// A callback already queued by the replaced run must not stop or settle the new worker.
		scheduled[0].callback();
		expect(second.events).toEqual(['start']);
		expect(settled).toEqual([]);

		scheduled[1].callback();
		expect(second.events).toEqual(['start', 'stop', 'kill']);
		expect(settled).toEqual(['second']);
		expect(controller.running).toBe(false);
	});

	it('kills an active worker on cancellation and rejects its already-queued timer', () => {
		let timeout: (() => void) | null = null;
		const events: string[] = [];
		let settled = false;
		const controller = new graphExplorer.TimedLayoutController({
			schedule: (callback) => {
				timeout = callback;
				return 1;
			},
			clear: () => events.push('clear')
		});
		controller.start(
			{
				start: () => events.push('start'),
				stop: () => events.push('stop'),
				kill: () => events.push('kill')
			},
			500,
			() => (settled = true)
		);

		controller.cancel();
		expect(events).toEqual(['start', 'clear', 'stop', 'kill']);
		expect(controller.running).toBe(false);
		expect(timeout).not.toBeNull();
		(timeout as unknown as () => void)();
		expect(settled).toBe(false);
	});

	it('kills a worker that refuses to start and preserves the startup error', () => {
		const events: string[] = [];
		const controller = new graphExplorer.TimedLayoutController({
			schedule: () => {
				throw new Error('a failed worker must not schedule a timer');
			},
			clear: () => events.push('clear')
		});

		expect(() =>
			controller.start(
				{
					start: () => {
						throw new Error('worker startup failed');
					},
					stop: () => events.push('stop'),
					kill: () => events.push('kill')
				},
				500,
				() => events.push('settled')
			)
		).toThrow('worker startup failed');
		expect(events).toEqual(['kill']);
		expect(controller.running).toBe(false);
	});

	it('kills a started worker if its stop timer cannot be scheduled', () => {
		const events: string[] = [];
		const controller = new graphExplorer.TimedLayoutController({
			schedule: () => {
				throw new Error('timer scheduling failed');
			},
			clear: () => events.push('clear')
		});

		expect(() =>
			controller.start(
				{
					start: () => events.push('start'),
					stop: () => events.push('stop'),
					kill: () => events.push('kill')
				},
				500,
				() => events.push('settled')
			)
		).toThrow('timer scheduling failed');
		expect(events).toEqual(['start', 'stop', 'kill']);
		expect(controller.running).toBe(false);
	});
});

describe('collectSemanticTypes', () => {
	it('returns the distinct non-null types, sorted', () => {
		const g = graphWith([
			['A', { semanticType: 'Gene' }],
			['B', { semanticType: 'Disease' }],
			['C', { semanticType: 'Gene' }],
			['D', { semanticType: null }]
		]);
		expect(collectSemanticTypes(g)).toEqual(['Disease', 'Gene']);
	});
});

describe('neighborSet', () => {
	it('includes the node itself and its adjacent nodes', () => {
		const g = graphWith([
			['A', {}],
			['B', {}],
			['C', {}]
		]);
		g.addEdge('A', 'B');
		expect(neighborSet(g, 'A')).toEqual(new Set(['A', 'B']));
		expect(neighborSet(g, 'C')).toEqual(new Set(['C']));
	});
});

describe('findNode', () => {
	const g = graphWith([
		['C3262', { label: 'Neoplasm' }],
		['C9305', { label: 'Malignant Neoplasm' }]
	]);

	it('matches a code case-insensitively', () => {
		expect(findNode(g, 'c3262')).toBe('C3262');
	});

	it('matches a label substring', () => {
		expect(findNode(g, 'malignant')).toBe('C9305');
	});

	it('returns null for a blank term or no match', () => {
		expect(findNode(g, '   ')).toBeNull();
		expect(findNode(g, 'nonexistent')).toBeNull();
	});

	it('handles nodes without a label attribute gracefully', () => {
		// A node without a label attribute triggers the '??' fallback to ''.
		const g2 = graphWith([['C0', { label: null as unknown as string }]]);
		expect(findNode(g2, 'non-existent')).toBeNull();
		// Code match still works.
		expect(findNode(g2, 'c0')).toBe('C0');
	});
});

describe('reduceNodeAppearance', () => {
	const base = {
		centerCode: 'CENTER',
		semanticType: 'Gene',
		degree: 3,
		hiddenTypes: new Set<string>(),
		hideIsolated: false,
		selectedCode: null,
		hovered: null,
		hoveredNeighbors: null
	};

	it('draws the center as a circle and never hides it', () => {
		const res = reduceNodeAppearance({
			...base,
			node: 'CENTER',
			hiddenTypes: new Set(['Gene']),
			hideIsolated: true,
			degree: 0
		});
		expect(res.type).toBe('circle');
		expect(res.hidden).toBeUndefined();
	});

	it('hides a node whose semantic type is toggled off', () => {
		const res = reduceNodeAppearance({ ...base, node: 'N1', hiddenTypes: new Set(['Gene']) });
		expect(res.hidden).toBe(true);
	});

	it('hides an isolated node only when the isolate filter is on', () => {
		expect(reduceNodeAppearance({ ...base, node: 'N1', degree: 0 }).hidden).toBeUndefined();
		expect(
			reduceNodeAppearance({ ...base, node: 'N1', degree: 0, hideIsolated: true }).hidden
		).toBe(true);
	});

	it('highlights the selected node', () => {
		expect(reduceNodeAppearance({ ...base, node: 'N1', selectedCode: 'N1' }).highlighted).toBe(
			true
		);
	});

	it('dims non-neighbors of the hovered node and clears their label', () => {
		const res = reduceNodeAppearance({
			...base,
			node: 'FAR',
			hovered: 'H',
			hoveredNeighbors: new Set(['H', 'NEAR'])
		});
		expect(res.color).toBe(DIMMED_COLOR);
		expect(res.label).toBe('');
	});

	it('leaves a neighbor of the hovered node undimmed', () => {
		const res = reduceNodeAppearance({
			...base,
			node: 'NEAR',
			hovered: 'H',
			hoveredNeighbors: new Set(['H', 'NEAR'])
		});
		expect(res.color).toBeUndefined();
	});
});

describe('reduceEdgeAppearance', () => {
	it('does nothing when nothing is hovered', () => {
		expect(reduceEdgeAppearance({ hovered: null, source: 'A', target: 'B' })).toEqual({});
	});

	it('hides an edge not incident to the hovered node', () => {
		expect(reduceEdgeAppearance({ hovered: 'H', source: 'A', target: 'B' })).toEqual({
			hidden: true
		});
	});

	it('keeps an edge incident to the hovered node', () => {
		expect(reduceEdgeAppearance({ hovered: 'A', source: 'A', target: 'B' })).toEqual({});
	});
});

describe('minimapBounds', () => {
	it('returns null for an empty graph', () => {
		expect(minimapBounds(new Graph())).toBeNull();
	});

	it('computes the bounding box of node positions', () => {
		const g = graphWith([
			['A', { x: -2, y: 1 }],
			['B', { x: 4, y: 9 }]
		]);
		expect(minimapBounds(g)).toEqual({ minX: -2, minY: 1, spanX: 6, spanY: 8 });
	});

	it('clamps a zero span to 1 (single node / collinear) to avoid divide-by-zero', () => {
		const g = graphWith([['A', { x: 3, y: 3 }]]);
		expect(minimapBounds(g)).toEqual({ minX: 3, minY: 3, spanX: 1, spanY: 1 });
	});

	it('does not extend the max with a node positioned inside the current bounds', () => {
		// Outer node first, then inner node.
		const g = graphWith([
			['Outer', { x: -10, y: -10 }],
			['Inner', { x: 0, y: 0 }],
			['Outer2', { x: 10, y: 10 }]
		]);
		const bounds = minimapBounds(g);
		expect(bounds).toEqual({ minX: -10, minY: -10, spanX: 20, spanY: 20 });
	});

	it('handles an interior node that does not extend max bounds', () => {
		// Max-only interior: node C's x/y are between A and B but less than B.
		const g = graphWith([
			['A', { x: 0, y: 0 }],
			['B', { x: 10, y: 10 }],
			['C', { x: 5, y: 5 }]
		]);
		const bounds = minimapBounds(g);
		expect(bounds).toEqual({ minX: 0, minY: 0, spanX: 10, spanY: 10 });
	});
});

describe('projectToMinimap', () => {
	const b = { minX: 0, minY: 0, spanX: 10, spanY: 10 };
	const dims = { width: 100, height: 100, pad: 10 };

	it('maps the min corner to the padded top-left, flipping y', () => {
		// min-x -> left pad; min-y (graph bottom) -> canvas bottom (height - pad).
		expect(projectToMinimap(0, 0, b, dims)).toEqual({ x: 10, y: 90 });
	});

	it('maps the max corner to the padded bottom-right, flipping y', () => {
		expect(projectToMinimap(10, 10, b, dims)).toEqual({ x: 90, y: 10 });
	});
});
