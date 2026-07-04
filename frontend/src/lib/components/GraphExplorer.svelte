<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Sigma from 'sigma';
	import forceAtlas2 from 'graphology-layout-forceatlas2';
	import type Graph from 'graphology';
	import { getNeighborhood } from '$lib/api';
	import type { Neighborhood } from '$lib/types';
	import {
		createGraph,
		mergeNeighborhood,
		assignAnalytics,
		degreeToSize,
		communityColor,
		KIND_COLOR,
		type AnalyticsSummary,
		type NodeAttrs
	} from '$lib/graph/neighborhood-graph';

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
	let stats = $state<AnalyticsSummary>({ communityCount: 0, topByDegree: [] });
	let nodeCount = $state(0);
	let edgeCount = $state(0);
	let selected = $state<NodeAttrs | null>(null);
	let hovered = $state<string | null>(null);
	let expanding = $state(false);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let search = $state('');
	let fullscreen = $state(false);

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
		if (g.size > 0) {
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
		restyle(g);
	}

	async function expand(target: string) {
		if (!graph || expanding) return;
		if (graph.hasNode(target) && graph.getNodeAttribute(target, 'expanded')) return;
		expanding = true;
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
				defaultEdgeType: 'arrow',
				labelDensity: 0.5,
				labelRenderedSizeThreshold: 8,
				minCameraRatio: 0.05,
				maxCameraRatio: 4
			});
			setupReducers(sigma);

			sigma.on('clickNode', ({ node }) => {
				selected = graph?.getNodeAttributes(node) as NodeAttrs;
				sigma?.refresh();
			});
			sigma.on('doubleClickNode', ({ node, event }) => {
				event.preventSigmaDefault();
				void expand(node);
			});
			sigma.on('enterNode', ({ node }) => {
				hovered = node;
				sigma?.refresh();
			});
			sigma.on('leaveNode', () => {
				hovered = null;
				sigma?.refresh();
			});
			sigma.on('clickStage', () => {
				selected = null;
				sigma?.refresh();
			});
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

	$effect(() => {
		// Re-color when the color mode changes.
		void colorMode;
		if (graph) {
			restyle(graph);
			sigma?.refresh();
		}
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

		<!-- Side panel -->
		<div class="w-64 shrink-0 overflow-y-auto border-l border-default p-3 text-sm">
			{#if selected}
				<div class="mb-4">
					<h4 class="font-semibold text-default">{selected.label}</h4>
					<p class="mt-0.5 font-mono text-xs text-muted">{selected.code}</p>
					{#if selected.semanticType}
						<span
							class="mt-1 inline-block rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
							>{selected.semanticType}</span
						>
					{/if}
					<dl class="mt-2 grid grid-cols-2 gap-1 text-xs text-muted">
						<dt>Degree</dt>
						<dd class="text-right text-default">{selected.degree ?? 0}</dd>
						<dt>Community</dt>
						<dd class="text-right text-default">#{(selected.community ?? 0) + 1}</dd>
					</dl>
					<div class="mt-3 flex flex-col gap-1.5">
						<button
							type="button"
							class="rounded-md bg-primary-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
							disabled={expanding || selected.expanded}
							onclick={() => selected && expand(selected.code)}
						>
							{selected.expanded ? 'Expanded' : 'Expand node'}
						</button>
						<button
							type="button"
							class="rounded-md border border-default px-2.5 py-1.5 text-xs font-medium text-secondary hover:bg-subtle"
							onclick={() => selected && goto(resolve('/repositories/ncit/[code]', { code: selected.code }))}
						>
							Open concept →
						</button>
					</div>
				</div>
			{:else}
				<p class="mb-4 text-xs text-subtle">
					Click a node to inspect it. Double-click to expand its neighborhood.
				</p>
			{/if}

			<div class="border-t border-default pt-3">
				<h5 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Network</h5>
				<dl class="grid grid-cols-2 gap-1 text-xs text-muted">
					<dt>Nodes</dt>
					<dd class="text-right text-default">{nodeCount}</dd>
					<dt>Edges</dt>
					<dd class="text-right text-default">{edgeCount}</dd>
					<dt>Communities</dt>
					<dd class="text-right text-default">{stats.communityCount}</dd>
				</dl>
				{#if stats.topByDegree.length}
					<h5 class="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-muted">
						Most connected
					</h5>
					<ul class="flex flex-col gap-1 text-xs">
						{#each stats.topByDegree as n (n.code)}
							<li class="flex items-center justify-between gap-2">
								<button
									type="button"
									class="min-w-0 truncate text-left text-secondary hover:text-primary-600"
									onclick={() => {
										search = n.code;
										focusNode();
									}}>{n.label}</button
								>
								<span class="shrink-0 tabular-nums text-subtle">{n.degree}</span>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<div class="mt-3 border-t border-default pt-3">
				<h5 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Edge types</h5>
				<ul class="flex flex-col gap-1 text-xs text-muted">
					{#each Object.entries(KIND_COLOR) as [kind, color] (kind)}
						<li class="flex items-center gap-2">
							<span class="inline-block h-2.5 w-4 rounded-sm" style:background={color}></span>
							{kind}
						</li>
					{/each}
				</ul>
			</div>
		</div>
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
</style>
