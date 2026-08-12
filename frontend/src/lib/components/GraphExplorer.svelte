<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Sigma from 'sigma';
	import {
		DEFAULT_EDGE_ARROW_HEAD_PROGRAM_OPTIONS,
		NodeCircleProgram,
		createNodeCompoundProgram,
		drawDiscNodeLabel,
		drawStraightEdgeLabel
	} from 'sigma/rendering';
	import { createEdgeCurveProgram } from '@sigma/edge-curve';
	import { downloadAsImage } from '@sigma/export-image';
	import forceAtlas2 from 'graphology-layout-forceatlas2';
	import ForceAtlas2LayoutSupervisor from 'graphology-layout-forceatlas2/worker';
	import NoverlapLayoutSupervisor from 'graphology-layout-noverlap/worker';
	import type Graph from 'graphology';
	import { getNeighborhood, getUberonNeighborhood } from '$lib/api';
	import type { Neighborhood } from '$lib/types';
	import {
		createGraph,
		mergeNeighborhood,
		assignAnalytics,
		degreeToSize,
		type AnalyticsSummary,
		type NodeAttrs
	} from '$lib/graph/neighborhood-graph';
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
		nodeHiddenByFilters,
		applyGraphLabelTheme,
		applyGraphLabelPolicy,
		ellipsizeGraphLabel,
		forceAtlasLayoutBudget,
		graphLabelBounds,
		graphLabelPolicy,
		graphLabelTheme,
		GraphLabelCollisionIndex,
		TimedLayoutController,
		AsyncRequestOwner,
		type AsyncRequestLease,
		type LayoutWorker
	} from '$lib/graph/graph-explorer';
	import GraphSidePanel from '$lib/components/GraphSidePanel.svelte';
	import GraphMinimap from '$lib/components/GraphMinimap.svelte';
	import GraphCanvasState from '$lib/components/GraphCanvasState.svelte';
	import { theme } from '$lib/stores/theme.svelte';

	interface Props {
		/** Center concept code. */
		code: string;
		/** Optional pre-fetched neighborhood to seed without a round-trip. */
		initial?: Neighborhood | null;
		height?: string;
		repository?: 'ncit' | 'uberon';
	}

	let { code, initial = null, height = '32rem', repository = 'ncit' }: Props = $props();
	const legacyControlClass = $derived(repository === 'ncit' ? '' : 'hidden');

	async function fetchNeighborhood(target: string, signal?: AbortSignal): Promise<Neighborhood> {
		if (repository === 'ncit') return getNeighborhood(target, 1, undefined, signal);
		const raw = await getUberonNeighborhood(target, 1, undefined, signal);
		return {
			...raw,
			nodes: raw.nodes.map((node) => ({
				...node,
				semantic_type: node.source === 'cl' ? 'Cell Ontology' : 'Uberon',
				representation_status: null
			})),
			edges: raw.edges.map((edge) => ({
				...edge,
				kind: edge.kind === 'subClassOf' ? 'subClassOf' : 'role'
			}))
		};
	}

	let container = $state<HTMLDivElement | null>(null);
	let sigma = $state<Sigma | null>(null);
	let graph = $state<Graph | null>(null);

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
	let showLegacyOnly = $state(false);
	let visibleNodeCount = $state(0);
	// Semantic types the user has toggled off (hidden). A reactive set: the sigma
	// reducer and the filter chips both read it.
	const hiddenTypes = new SvelteSet<string>();
	let menu = $state<{ x: number; y: number; node: NodeAttrs } | null>(null);
	let menuEl = $state<HTMLDivElement | null>(null);
	let semanticTypes = $state<string[]>([]);
	let showMinimap = $state(true);
	let layoutRunning = $state(false);
	// Bumped after any graph mutation so the minimap redraws.
	let graphVersion = $state(0);
	const layoutController = new TimedLayoutController();
	const requestOwner = new AsyncRequestOwner();
	const labelCollisionIndex = new GraphLabelCollisionIndex();
	const CollisionAwareCurvedArrowProgram = createEdgeCurveProgram({
		arrowHead: DEFAULT_EDGE_ARROW_HEAD_PROGRAM_OPTIONS,
		drawLabel: (context, edgeData, sourceData, targetData, settings) => {
			if (!edgeData.label) return;
			context.font = `${settings.edgeLabelWeight} ${settings.edgeLabelSize}px ${settings.edgeLabelFont}`;
			const width = context.measureText(edgeData.label).width;
			const angle = Math.atan2(targetData.y - sourceData.y, targetData.x - sourceData.x);
			const halfWidth =
				(Math.abs(Math.cos(angle)) * width +
					Math.abs(Math.sin(angle)) * settings.edgeLabelSize) /
				2;
			const halfHeight =
				(Math.abs(Math.sin(angle)) * width +
					Math.abs(Math.cos(angle)) * settings.edgeLabelSize) /
				2;
			const x = (sourceData.x + targetData.x) / 2;
			const y = (sourceData.y + targetData.y) / 2;
			if (
				labelCollisionIndex.claim({
					left: x - halfWidth - 2,
					top: y - halfHeight - 2,
					right: x + halfWidth + 2,
					bottom: y + halfHeight + 2
				})
			)
				drawStraightEdgeLabel(context, edgeData, sourceData, targetData, settings);
		}
	});
	class LegacyStatusRingProgram extends NodeCircleProgram {
		override processVisibleItem(
			nodeIndex: number,
			startIndex: number,
			data: Parameters<NodeCircleProgram['processVisibleItem']>[2]
		): void {
			super.processVisibleItem(nodeIndex, startIndex, {
				...data,
				size: data.size + 3,
				color: '#f59e0b'
			});
		}
	}
	const LegacyPrecoordNodeProgram = createNodeCompoundProgram([
		LegacyStatusRingProgram,
		NodeCircleProgram
	]);

	function drawThemeAwareNodeHover(
		context: CanvasRenderingContext2D,
		data: Parameters<typeof drawDiscNodeLabel>[1],
		settings: Parameters<typeof drawDiscNodeLabel>[2]
	): void {
		const labelTheme = graphLabelTheme(theme.current);
		const padding = 3;
		const labelSize = settings.labelSize;
		context.save();
		context.font = `${settings.labelWeight} ${labelSize}px ${settings.labelFont}`;
		context.fillStyle = labelTheme.hoverBackgroundColor;
		context.shadowBlur = 8;
		context.shadowColor = '#000000';
		if (data.label) {
			const boxHeight = labelSize + padding * 2;
			const radius = Math.max(data.size, labelSize / 2) + padding;
			const halfHeight = boxHeight / 2;
			const xDelta = Math.sqrt(Math.max(0, radius ** 2 - halfHeight ** 2));
			const right = data.x + radius + context.measureText(data.label).width + padding * 2;
			const angle = Math.asin(Math.min(1, halfHeight / radius));
			context.beginPath();
			context.moveTo(data.x + xDelta, data.y + halfHeight);
			context.lineTo(right, data.y + halfHeight);
			context.lineTo(right, data.y - halfHeight);
			context.lineTo(data.x + xDelta, data.y - halfHeight);
			context.arc(data.x, data.y, radius, angle, -angle);
			context.closePath();
			context.fill();
		} else {
			context.beginPath();
			context.arc(data.x, data.y, data.size + padding, 0, Math.PI * 2);
			context.fill();
		}
		context.shadowBlur = 0;
		drawDiscNodeLabel(context, data, settings);
		context.restore();
	}

	// Session-stable semantic-type colorer (read inside the sigma reducer, not markup).
	const semanticColorer = makeSemanticColorer();

	function nodeColor(attrs: NodeAttrs): string {
		return nodeColorForMode(colorMode, attrs, semanticColorer);
	}

	function runLayout(g: Graph) {
		layoutController.cancel();
		layoutRunning = false;
		seedPositions(g);
		ensureFinite(g);

		let worker: LayoutWorker | null = null;
		if (g.size > 0 && layoutMode === 'forceatlas2') {
			worker = new ForceAtlas2LayoutSupervisor(g, {
				settings: {
					...forceAtlas2.inferSettings(g),
					gravity: 1.4,
					scalingRatio: 12,
					barnesHutOptimize: g.order > 120
				}
			});
		}
		if (g.order > 0 && layoutMode === 'noverlap') {
			worker = new NoverlapLayoutSupervisor(g, { settings: { margin: 4, ratio: 1.2 } });
		}
		if (!worker) return;

		const { durationMs } = forceAtlasLayoutBudget(g.order, g.size);
		try {
			layoutController.start(worker, durationMs, () => {
				layoutRunning = layoutController.running;
				if (g !== graph) return;
				ensureFinite(g);
				graphVersion += 1;
				sigma?.refresh();
			});
			layoutRunning = layoutController.running;
		} catch (reason) {
			layoutRunning = false;
			throw reason;
		}
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
		semanticTypes = collectSemanticTypes(g);
		restyle(g);
		refreshVisibility(g);
		graphVersion += 1;
	}

	function nodeIsHidden(g: Graph, node: string): boolean {
		const attrs = g.getNodeAttributes(node) as NodeAttrs;
		return nodeHiddenByFilters({
			isCenter: node === code,
			semanticType: attrs.semanticType,
			degree: g.degree(node),
			hiddenTypes,
			hideIsolated,
			representationStatus: attrs.representationStatus,
			showLegacyOnly: repository === 'ncit' && showLegacyOnly
		});
	}

	function refreshVisibility(g: Graph) {
		let count = 0;
		g.forEachNode((node) => {
			if (!nodeIsHidden(g, node)) count += 1;
		});
		visibleNodeCount = count;
		if (selected && nodeIsHidden(g, selected.code)) selected = null;
	}

	function canExpand(candidate: Graph, target: string): boolean {
		// Pseudo-nodes (e.g. a caDSR "cde:<id>:<ver>" seed) aren't NCIt concepts, so
		// they have no /neighborhood — skip rather than fetch a guaranteed 404.
		return (
			(repository === 'uberon' || !target.includes(':')) &&
			!(candidate.hasNode(target) && candidate.getNodeAttribute(target, 'expanded'))
		);
	}

	function ownsGraph(lease: AsyncRequestLease, candidate: Graph): boolean {
		return lease.isCurrent() && graph === candidate;
	}

	async function expand(target: string) {
		if (!graph || expanding || !canExpand(graph, target)) return;
		const activeGraph = graph;
		const lease = requestOwner.lease();
		expanding = true;
		error = null; // a prior transient error must not stick across expansions
		try {
			const nb = await fetchNeighborhood(target, lease.signal);
			if (!ownsGraph(lease, activeGraph)) return;
			layoutController.cancel();
			layoutRunning = false;
			mergeNeighborhood(activeGraph, nb);
			refreshStats(activeGraph);
			runLayout(activeGraph);
			sigma?.refresh();
		} catch (err) {
			if (ownsGraph(lease, activeGraph))
				error = err instanceof Error ? err.message : String(err);
		} finally {
			if (ownsGraph(lease, activeGraph)) expanding = false;
			lease.release();
		}
	}

	function neighbors(node: string): Set<string> {
		return neighborSet(graph!, node);
	}

	function setupReducers(s: Sigma) {
		s.setSetting('nodeReducer', (node, data) => {
			const g = graph;
			return {
				...data,
				...reduceNodeAppearance({
					node,
					centerCode: code,
					semanticType:
						g && node !== code
							? (g.getNodeAttribute(node, 'semanticType') as string | null)
							: null,
					degree: g && node !== code ? g.degree(node) : 0,
					hiddenTypes,
					hideIsolated,
					selectedCode: selected?.code ?? null,
					hovered,
					hoveredNeighbors: hovered ? neighbors(hovered) : null,
					representationStatus: g
						? (g.getNodeAttribute(
								node,
								'representationStatus'
							) as NodeAttrs['representationStatus'])
						: null,
					showLegacyOnly: repository === 'ncit' && showLegacyOnly
				})
			};
		});
		s.setSetting('edgeReducer', (edge, data) => {
			if (!graph) return { ...data };
			const [src, tgt] = graph.extremities(edge);
			return {
				...data,
				...reduceEdgeAppearance({
					hovered,
					source: src,
					target: tgt,
					sourceHidden: nodeIsHidden(graph, src),
					targetHidden: nodeIsHidden(graph, tgt)
				})
			};
		});
	}

	function setupInteractions(s: Sigma) {
		let dragged: string | null = null;
		s.on('beforeRender', () => labelCollisionIndex.reset());

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
			applyGraphLabelPolicy(s, ratio);
			menu = null;
		});
	}

	function disposeGraph(): void {
		layoutController.cancel();
		layoutRunning = false;
		sigma?.kill();
		sigma = null;
		graph = null;
		expanding = false;
		selected = null;
		hovered = null;
		menu = null;
	}

	async function init(
		nextCode: string,
		nextInitial: Neighborhood | null,
		targetContainer: HTMLDivElement,
		lease: AsyncRequestLease
	) {
		loading = true;
		error = null;
		const nextGraph = createGraph();
		graph = nextGraph;
		try {
			const nb = nextInitial ?? (await fetchNeighborhood(nextCode, lease.signal));
			if (!lease.isCurrent() || graph !== nextGraph) return;
			mergeNeighborhood(nextGraph, nb);
			seedPositions(nextGraph);
			refreshStats(nextGraph);
			const labelTheme = graphLabelTheme(theme.current);

			const renderer = new Sigma(nextGraph, targetContainer, {
				renderEdgeLabels: true,
				defaultEdgeType: 'curved',
				nodeProgramClasses: {
					'legacy-precoordinated': LegacyPrecoordNodeProgram
				},
				edgeProgramClasses: { curved: CollisionAwareCurvedArrowProgram },
				...graphLabelPolicy(1),
				labelColor: { color: labelTheme.labelColor },
				edgeLabelColor: { color: labelTheme.edgeLabelColor },
				defaultDrawNodeLabel: (context, data, settings) => {
					if (!data.label) return;
					const label = ellipsizeGraphLabel(data.label);
					context.font = `${settings.labelWeight} ${settings.labelSize}px ${settings.labelFont}`;
					const bounds = graphLabelBounds({
						x: data.x,
						y: data.y,
						nodeSize: data.size,
						labelSize: settings.labelSize,
						labelWidth: context.measureText(label).width
					});
					if (labelCollisionIndex.claim(bounds))
						drawDiscNodeLabel(context, { ...data, label }, settings);
				},
				defaultDrawNodeHover: drawThemeAwareNodeHover,
				minCameraRatio: 0.05,
				maxCameraRatio: 4
			});
			if (!lease.isCurrent() || graph !== nextGraph) {
				renderer.kill();
				return;
			}
			sigma = renderer;
			setupReducers(renderer);
			setupInteractions(renderer);
			runLayout(nextGraph);
		} catch (err) {
			if (!lease.isCurrent() || graph !== nextGraph) return;
			layoutController.cancel();
			layoutRunning = false;
			sigma?.kill();
			sigma = null;
			error = err instanceof Error ? err.message : String(err);
		} finally {
			if (lease.isCurrent() && graph === nextGraph) loading = false;
			lease.release();
		}
	}

	function focusNode() {
		if (!graph || !sigma) return;
		const found = findNode(graph, search);
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
		error = null;
		try {
			runLayout(graph);
			sigma?.refresh();
		} catch (reason) {
			error = reason instanceof Error ? reason.message : String(reason);
		}
	}

	function applyLayout(mode: 'forceatlas2' | 'noverlap') {
		layoutMode = mode;
		relayout();
	}

	function exportPng() {
		if (sigma) void downloadAsImage(sigma, { fileName: `${repository}-${code}-graph` });
	}

	function toggleType(t: string) {
		if (hiddenTypes.has(t)) hiddenTypes.delete(t);
		else hiddenTypes.add(t);
		if (graph) refreshVisibility(graph);
		sigma?.refresh();
	}

	function toggleIsolated() {
		hideIsolated = !hideIsolated;
		if (graph) refreshVisibility(graph);
		sigma?.refresh();
	}

	function toggleLegacyOnly() {
		showLegacyOnly = !showLegacyOnly;
		if (graph) refreshVisibility(graph);
		sigma?.refresh();
	}

	function unpinNode(nodeCode: string) {
		graph?.removeNodeAttribute(nodeCode, 'fixed');
	}

	function menuAction(action: 'expand' | 'open' | 'unpin' | 'hide-type') {
		if (!menu) return;
		const { node } = menu;
		if (action === 'expand') void expand(node.code);
		else if (action === 'open')
			void goto(
				repository === 'ncit'
					? resolve('/repositories/ncit/[code]', { code: node.code })
					: resolve('/repositories/uberon/[curie]', { curie: node.code })
			);
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
		const currentTheme = theme.current;
		if (sigma) applyGraphLabelTheme(sigma, currentTheme);
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

	$effect(() => {
		const nextCode = code;
		const nextInitial = initial;
		const targetContainer = container;
		if (!targetContainer) return;
		requestOwner.replace();
		const lease = requestOwner.lease();
		untrack(() => {
			disposeGraph();
			void init(nextCode, nextInitial, targetContainer, lease);
		});
		return () => requestOwner.replace();
	});

	onDestroy(() => {
		requestOwner.replace();
		disposeGraph();
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
			class="{legacyControlClass} rounded-lg border border-default px-2 py-1 text-xs {showLegacyOnly
				? 'bg-amber-500 text-neutral-950'
				: 'text-secondary hover:bg-subtle'}"
			onclick={toggleLegacyOnly}
			title="Show only nodes with the published legacy pre-coordinated marker"
		>
			Legacy pre-coordinated only
		</button>
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
			class="rounded-lg border border-default px-2 py-1 text-xs {showMinimap
				? 'bg-primary-600 text-white'
				: 'text-secondary hover:bg-subtle'}"
			onclick={() => (showMinimap = !showMinimap)}
			title="Toggle minimap">Minimap</button
		>
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
		<div
			bind:this={container}
			class="graph-canvas relative flex-1"
			aria-busy={layoutRunning}
		></div>

		{#if !loading && showMinimap && sigma && graph}
			<GraphMinimap {graph} {sigma} version={graphVersion} />
		{/if}

		<GraphCanvasState {loading} {error} {visibleNodeCount} {expanding} />

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
			onopen={(c) => goto(repository === 'ncit' ? resolve('/repositories/ncit/[code]', { code: c }) : resolve('/repositories/uberon/[curie]', { curie: c }))}
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
