<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Sigma from 'sigma';
	import { EdgeCurvedArrowProgram } from '@sigma/edge-curve';
	import { downloadAsImage } from '@sigma/export-image';
	import forceAtlas2 from 'graphology-layout-forceatlas2';
	import noverlap from 'graphology-layout-noverlap';
	import type Graph from 'graphology';
	import { getNeighborhood } from '$lib/api';
	import type { Neighborhood } from '$lib/types';
	import {
		createGraph,
		mergeNeighborhood,
		assignAnalytics,
		degreeToSize,
		communityColor,
		type AnalyticsSummary,
		type NodeAttrs
	} from '$lib/graph/neighborhood-graph';
	import GraphSidePanel from '$lib/components/GraphSidePanel.svelte';

	interface Props {
		/** Center concept code. */
		code: string;
		/** Optional pre-fetched neighborhood to seed without a round-trip. */
		initial?: Neighborhood | null;
		height?: string;
	}

	let { code, initial = null, height = '32rem' }: Props = $props();

	let container = $state<HTMLDivElement | null>(null);
	let sigma: Sigma | null = null;
	let graph: Graph | null = null;

	let colorMode = $state<'community' | 'semantic'>('community');
	let layoutMode = $state<'forceatlas2' | 'noverlap'>('forceatlas2');
	let stats = $state<AnalyticsSummary>({
		communityCount: 0,
		topByDegree: [],
		topByBetweenness: []
	});
	let nodeCount = $state(0);
	let edgeCount = $state(0);
	let selected = $state<NodeAttrs | null>(null);
	let hovered = $state<string | null>(null);
	let expanding = $state(false);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let search = $state('');
	let fullscreen = $state(false);
	let hideIsolated = $state(false);
	// Semantic types the user has toggled off (hidden). A reactive set: the sigma
	// reducer and the filter chips both read it.
	const hiddenTypes = new SvelteSet<string>();
	let menu = $state<{ x: number; y: number; node: NodeAttrs } | null>(null);
	let menuEl = $state<HTMLDivElement | null>(null);
	let semanticTypes = $state<string[]>([]);

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
	// Non-reactive color cache (read inside the sigma reducer, never in markup).
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	const semanticColors = new Map<string, string>();
	function semanticColor(t: string | null): string {
		if (!t) return '#94a3b8';
		if (!semanticColors.has(t)) {
			semanticColors.set(t, SEMANTIC_PALETTE[semanticColors.size % SEMANTIC_PALETTE.length]);
		}
		return semanticColors.get(t) as string;
	}

	function nodeColor(attrs: NodeAttrs): string {
		return colorMode === 'community'
			? communityColor(attrs.community)
			: semanticColor(attrs.semanticType);
	}

	/** Seed positions for freshly added nodes so the layout has somewhere to start. */
	function seedPositions(g: Graph) {
		g.forEachNode((n, attrs) => {
			if (typeof attrs.x !== 'number' || typeof attrs.y !== 'number') {
				const angle = Math.random() * 2 * Math.PI;
				const r = 0.5 + Math.random();
				g.setNodeAttribute(n, 'x', Math.cos(angle) * r);
				g.setNodeAttribute(n, 'y', Math.sin(angle) * r);
			}
		});
	}

	/** Guarantee every node has finite x/y — sigma throws otherwise. */
	function ensureFinite(g: Graph) {
		g.forEachNode((n, attrs) => {
			if (!Number.isFinite(attrs.x as number) || !Number.isFinite(attrs.y as number)) {
				const angle = Math.random() * 2 * Math.PI;
				g.setNodeAttribute(n, 'x', Math.cos(angle));
				g.setNodeAttribute(n, 'y', Math.sin(angle));
			}
		});
	}

	function runLayout(g: Graph) {
		seedPositions(g);
		if (g.size > 0 && layoutMode === 'forceatlas2') {
			forceAtlas2.assign(g, {
				iterations: 220,
				settings: {
					...forceAtlas2.inferSettings(g),
					gravity: 1.4,
					scalingRatio: 12,
					barnesHutOptimize: g.order > 120
				}
			});
		}
		if (g.order > 0 && layoutMode === 'noverlap') {
			// Spread first (a coordinate-less graph collapses to the origin), then remove
			// node overlaps — a compact, grid-like alternative to the force layout.
			forceAtlas2.assign(g, { iterations: 60, settings: forceAtlas2.inferSettings(g) });
			noverlap.assign(g, { maxIterations: 120, settings: { margin: 4, ratio: 1.2 } });
		}
		ensureFinite(g);
	}

	function restyle(g: Graph) {
		g.forEachNode((n, attrs) => {
			const a = attrs as NodeAttrs;
			g.mergeNodeAttributes(n, {
				size: n === code ? degreeToSize((a.degree ?? 1) + 6) : degreeToSize(a.degree ?? 1),
				color: nodeColor(a)
			});
		});
	}

	function refreshStats(g: Graph) {
		stats = assignAnalytics(g);
		nodeCount = g.order;
		edgeCount = g.size;
		// Transient, non-reactive: collected once then spread into semanticTypes.
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const types = new Set<string>();
		g.forEachNode((_n, attrs) => {
			const t = (attrs as NodeAttrs).semanticType;
			if (t) types.add(t);
		});
		semanticTypes = [...types].sort();
		restyle(g);
	}

	async function expand(target: string) {
		if (!graph || expanding) return;
		// Pseudo-nodes (e.g. a caDSR "cde:<id>:<ver>" seed) aren't NCIt concepts, so
		// they have no /neighborhood — skip rather than fetch a guaranteed 404.
		if (target.includes(':')) return;
		if (graph.hasNode(target) && graph.getNodeAttribute(target, 'expanded')) return;
		expanding = true;
		error = null; // a prior transient error must not stick across expansions
		try {
			const nb = await getNeighborhood(target);
			mergeNeighborhood(graph, nb);
			runLayout(graph);
			refreshStats(graph);
			sigma?.refresh();
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			expanding = false;
		}
	}

	function neighbors(node: string): Set<string> {
		// Transient, non-reactive lookup set rebuilt per reducer call.
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const set = new Set<string>([node]);
		graph?.forEachNeighbor(node, (n) => set.add(n));
		return set;
	}

	function setupReducers(s: Sigma) {
		s.setSetting('nodeReducer', (node, data) => {
			const res = { ...data };
			if (node === code) res.type = 'circle';
			// Filters: hide nodes of a toggled-off semantic type, and (optionally)
			// degree-0 nodes. The center is always kept visible.
			if (node !== code && graph) {
				const semType = graph.getNodeAttribute(node, 'semanticType') as string | null;
				if (semType && hiddenTypes.has(semType)) res.hidden = true;
				if (hideIsolated && graph.degree(node) === 0) res.hidden = true;
			}
			if (selected && node === selected.code) {
				res.highlighted = true;
			}
			if (hovered) {
				const near = neighbors(hovered);
				if (!near.has(node)) {
					res.color = '#cbd5e1';
					res.label = '';
				}
			}
			return res;
		});
		s.setSetting('edgeReducer', (edge, data) => {
			const res = { ...data };
			if (hovered && graph) {
				const [src, tgt] = graph.extremities(edge);
				if (src !== hovered && tgt !== hovered) {
					res.hidden = true;
				}
			}
			return res;
		});
	}

	function setupInteractions(s: Sigma) {
		let dragged: string | null = null;

		s.on('clickNode', ({ node }) => {
			selected = graph?.getNodeAttributes(node) as NodeAttrs;
			menu = null;
			sigma?.refresh();
		});
		s.on('doubleClickNode', ({ node, event }) => {
			event.preventSigmaDefault();
			void expand(node);
		});
		s.on('enterNode', ({ node }) => {
			hovered = node;
			sigma?.refresh();
		});
		s.on('leaveNode', () => {
			hovered = null;
			sigma?.refresh();
		});
		s.on('clickStage', () => {
			selected = null;
			menu = null;
			sigma?.refresh();
		});

		// Right-click a node → context menu (positioned over the canvas).
		s.on('rightClickNode', ({ node, event }) => {
			event.preventSigmaDefault();
			event.original.preventDefault();
			menu = { x: event.x, y: event.y, node: graph?.getNodeAttributes(node) as NodeAttrs };
		});

		// Pin/drag: dragging fixes a node's position until released. Left button only —
		// right-click never emits a matching `up*`, which would strand the drag state.
		s.on('downNode', ({ node, event }) => {
			if (event.original instanceof MouseEvent && event.original.button !== 0) return;
			dragged = node;
			graph?.setNodeAttribute(node, 'highlighted', true);
		});
		s.on('moveBody', ({ event }) => {
			if (!dragged || !graph) return;
			const pos = s.viewportToGraph(event);
			graph.setNodeAttribute(dragged, 'x', pos.x);
			graph.setNodeAttribute(dragged, 'y', pos.y);
			// forceAtlas2 honors `fixed` (noverlap ignores it) — keep the dragged spot.
			graph.setNodeAttribute(dragged, 'fixed', true);
			// preventSigmaDefault suppresses camera pan during the drag (no need to
			// disable the camera, which would strand it disabled on a missed mouseup).
			event.preventSigmaDefault();
			event.original.stopPropagation();
		});
		const release = () => {
			if (dragged) graph?.removeNodeAttribute(dragged, 'highlighted');
			dragged = null;
		};
		s.on('upNode', release);
		s.on('upStage', release);

		// Zoom-scalable labels: reveal more labels as the camera zooms in. Also dismiss
		// the context menu on any pan/zoom so it never lingers detached from its node.
		s.getCamera().on('updated', ({ ratio }) => {
			s.setSetting('labelRenderedSizeThreshold', ratio < 0.6 ? 3 : 8);
			menu = null;
		});
	}

	async function init() {
		if (!container) return;
		loading = true;
		error = null;
		graph = createGraph();
		try {
			const nb = initial ?? (await getNeighborhood(code));
			mergeNeighborhood(graph, nb);
			runLayout(graph);
			refreshStats(graph);

			sigma = new Sigma(graph, container, {
				renderEdgeLabels: true,
				defaultEdgeType: 'curved',
				edgeProgramClasses: { curved: EdgeCurvedArrowProgram },
				labelDensity: 0.5,
				labelRenderedSizeThreshold: 8,
				minCameraRatio: 0.05,
				maxCameraRatio: 4
			});
			setupReducers(sigma);
			setupInteractions(sigma);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	function focusNode() {
		if (!graph || !sigma) return;
		const term = search.trim().toLowerCase();
		if (!term) return;
		let found: string | null = null;
		graph.forEachNode((n, attrs) => {
			if (found) return;
			const a = attrs as NodeAttrs;
			if (n.toLowerCase() === term || (a.label ?? '').toLowerCase().includes(term)) found = n;
		});
		if (found) {
			selected = graph.getNodeAttributes(found) as NodeAttrs;
			const pos = sigma.getNodeDisplayData(found);
			if (pos) sigma.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.4 }, { duration: 500 });
			sigma.refresh();
		}
	}

	function zoom(dir: 'in' | 'out') {
		const cam = sigma?.getCamera();
		if (!cam) return;
		if (dir === 'in') cam.animatedZoom({ duration: 300 });
		else cam.animatedUnzoom({ duration: 300 });
	}

	function fit() {
		sigma?.getCamera().animatedReset({ duration: 400 });
	}

	function relayout() {
		if (!graph) return;
		runLayout(graph);
		sigma?.refresh();
		fit();
	}

	function applyLayout(mode: 'forceatlas2' | 'noverlap') {
		layoutMode = mode;
		relayout();
	}

	function exportPng() {
		if (sigma) void downloadAsImage(sigma, { fileName: `ncit-${code}-graph` });
	}

	function toggleType(t: string) {
		if (hiddenTypes.has(t)) hiddenTypes.delete(t);
		else hiddenTypes.add(t);
		sigma?.refresh();
	}

	function toggleIsolated() {
		hideIsolated = !hideIsolated;
		sigma?.refresh();
	}

	function unpinNode(nodeCode: string) {
		graph?.removeNodeAttribute(nodeCode, 'fixed');
	}

	function menuAction(action: 'expand' | 'open' | 'unpin' | 'hide-type') {
		if (!menu) return;
		const { node } = menu;
		if (action === 'expand') void expand(node.code);
		else if (action === 'open') void goto(resolve('/repositories/ncit/[code]', { code: node.code }));
		else if (action === 'unpin') unpinNode(node.code);
		else if (action === 'hide-type' && node.semanticType) toggleType(node.semanticType);
		menu = null;
	}

	$effect(() => {
		// Re-color when the color mode changes.
		void colorMode;
		if (graph) {
			restyle(graph);
			sigma?.refresh();
		}
	});

	$effect(() => {
		// While the context menu is open, dismiss it on Escape or a pointer down outside
		// it (clicks on its own buttons are excluded so their action still fires).
		if (!menu) return;
		const onPointerDown = (e: PointerEvent) => {
			if (menuEl && !menuEl.contains(e.target as Node)) menu = null;
		};
		const onKey = (e: KeyboardEvent) => {
			if (e.key === 'Escape') menu = null;
		};
		window.addEventListener('pointerdown', onPointerDown, true);
		window.addEventListener('keydown', onKey);
		return () => {
			window.removeEventListener('pointerdown', onPointerDown, true);
			window.removeEventListener('keydown', onKey);
		};
	});

	onMount(() => {
		void init();
	});

	onDestroy(() => {
		sigma?.kill();
		sigma = null;
	});
</script>

<div
	class="rounded-xl border border-default bg-card shadow-sm"
	class:fixed={fullscreen}
	class:inset-4={fullscreen}
	class:z-50={fullscreen}
>
	<!-- Toolbar -->
	<div class="flex flex-wrap items-center gap-2 border-b border-default px-3 py-2">
		<div class="flex items-center gap-1">
			<button type="button" class="gx-btn" onclick={() => zoom('in')} title="Zoom in" aria-label="Zoom in">+</button>
			<button type="button" class="gx-btn" onclick={() => zoom('out')} title="Zoom out" aria-label="Zoom out">−</button>
			<button type="button" class="gx-btn" onclick={fit} title="Fit to view" aria-label="Fit">⤢</button>
			<button type="button" class="gx-btn" onclick={relayout} title="Re-run layout" aria-label="Re-layout">↻</button>
		</div>

		<div class="mx-1 h-5 w-px bg-neutral-300 dark:bg-neutral-700"></div>

		<div class="inline-flex overflow-hidden rounded-lg border border-default text-xs">
			<button
				type="button"
				class="px-2.5 py-1 {colorMode === 'community' ? 'bg-primary-600 text-white' : 'text-secondary hover:bg-subtle'}"
				onclick={() => (colorMode = 'community')}>Communities</button
			>
			<button
				type="button"
				class="px-2.5 py-1 {colorMode === 'semantic' ? 'bg-primary-600 text-white' : 'text-secondary hover:bg-subtle'}"
				onclick={() => (colorMode = 'semantic')}>Semantic type</button
			>
		</div>

		<div class="mx-1 h-5 w-px bg-neutral-300 dark:bg-neutral-700"></div>

		<label class="sr-only" for="gx-layout">Layout</label>
		<select
			id="gx-layout"
			class="rounded-lg border border-default bg-page-bg px-2 py-1 text-xs text-default focus:border-primary-500 focus:outline-none dark:bg-neutral-900"
			value={layoutMode}
			onchange={(e) => applyLayout(e.currentTarget.value as 'forceatlas2' | 'noverlap')}
			title="Layout preset"
		>
			<option value="forceatlas2">Force layout</option>
			<option value="noverlap">No-overlap</option>
		</select>
		<button
			type="button"
			class="rounded-lg border border-default px-2 py-1 text-xs {hideIsolated
				? 'bg-primary-600 text-white'
				: 'text-secondary hover:bg-subtle'}"
			onclick={toggleIsolated}
			title="Hide degree-0 nodes">Hide isolated</button
		>
		<button
			type="button"
			class="gx-btn"
			onclick={exportPng}
			title="Export as PNG"
			aria-label="Export as PNG">⭳</button
		>

		<form
			class="relative ml-auto"
			onsubmit={(e) => {
				e.preventDefault();
				focusNode();
			}}
		>
			<input
				type="search"
				bind:value={search}
				placeholder="Find node…"
				class="w-44 rounded-lg border border-default bg-page-bg py-1 pl-3 pr-3 text-xs text-default placeholder:text-subtle focus:border-primary-500 focus:outline-none dark:bg-neutral-900"
			/>
		</form>
		<button
			type="button"
			class="gx-btn"
			onclick={() => (fullscreen = !fullscreen)}
			title="Toggle fullscreen"
			aria-label="Fullscreen">{fullscreen ? '⤡' : '⤢'}</button
		>
	</div>

	<div class="relative flex" style:height={fullscreen ? 'calc(100vh - 8rem)' : height}>
		<!-- Canvas -->
		<div bind:this={container} class="graph-canvas relative flex-1"></div>

		{#if loading}
			<div class="absolute inset-0 flex items-center justify-center text-sm text-muted">
				Building graph…
			</div>
		{:else if error}
			<div class="absolute inset-0 flex items-center justify-center text-sm text-danger">
				{error}
			</div>
		{/if}
		{#if expanding}
			<div
				class="absolute left-3 top-3 rounded-md bg-primary-600 px-2 py-1 text-xs font-medium text-white shadow"
			>
				Expanding…
			</div>
		{/if}

		{#if menu}
			<!-- Right-click context menu, positioned over the canvas. -->
			<div
				bind:this={menuEl}
				class="absolute z-20 min-w-40 overflow-hidden rounded-lg border border-default bg-card text-xs shadow-lg"
				style:left="{menu.x}px"
				style:top="{menu.y}px"
			>
				<div class="truncate border-b border-default px-3 py-1.5 font-medium text-default">
					{menu.node.label}
				</div>
				<button type="button" class="gx-menu-item" onclick={() => menuAction('expand')}
					>Expand neighborhood</button
				>
				<button type="button" class="gx-menu-item" onclick={() => menuAction('open')}
					>Open concept →</button
				>
				<button type="button" class="gx-menu-item" onclick={() => menuAction('unpin')}
					>Unpin</button
				>
				{#if menu.node.semanticType}
					<button type="button" class="gx-menu-item" onclick={() => menuAction('hide-type')}
						>Hide type “{menu.node.semanticType}”</button
					>
				{/if}
			</div>
		{/if}

		<!-- Side panel -->
		<GraphSidePanel
			{selected}
			{expanding}
			{stats}
			{nodeCount}
			{edgeCount}
			{semanticTypes}
			{hiddenTypes}
			onexpand={(c) => expand(c)}
			onopen={(c) => goto(resolve('/repositories/ncit/[code]', { code: c }))}
			onfocus={(c) => {
				search = c;
				focusNode();
			}}
			ontoggletype={toggleType}
		/>
	</div>
</div>

<style>
	.graph-canvas {
		background: var(--nci-primary-50, #e8f4fc);
	}
	:global(.dark) .graph-canvas {
		background: var(--color-neutral-900, #171717);
	}
	.gx-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 1.75rem;
		width: 1.75rem;
		border-radius: 0.375rem;
		border: 1px solid var(--color-border, #e5e5e5);
		font-size: 0.9rem;
		color: var(--color-text-secondary, #404040);
	}
	.gx-btn:hover {
		background: var(--color-bg-subtle, #f5f5f5);
	}
	:global(.dark) .gx-btn {
		border-color: #404040;
		color: #d4d4d4;
	}
	:global(.dark) .gx-btn:hover {
		background: #262626;
	}
	.gx-menu-item {
		display: block;
		width: 100%;
		padding: 0.375rem 0.75rem;
		text-align: left;
		color: var(--color-text-secondary, #404040);
	}
	.gx-menu-item:hover {
		background: var(--color-bg-subtle, #f5f5f5);
	}
	:global(.dark) .gx-menu-item {
		color: #d4d4d4;
	}
	:global(.dark) .gx-menu-item:hover {
		background: #262626;
	}
</style>
