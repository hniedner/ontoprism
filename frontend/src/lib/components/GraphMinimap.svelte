<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type Graph from 'graphology';
	import type Sigma from 'sigma';
	import { minimapBounds, projectToMinimap } from '$lib/graph/graph-explorer';

	// A lightweight overview minimap: node positions drawn as dots into a small canvas,
	// with a rectangle marking the main view's current viewport. Orientation aid for
	// large expanded neighborhoods. Redrawn on camera moves and when `version` bumps
	// (the parent increments it after the graph changes).
	interface Props {
		graph: Graph;
		sigma: Sigma;
		version: number;
	}

	let { graph, sigma, version }: Props = $props();

	const W = 150;
	const H = 104;
	const PAD = 6;
	let canvas = $state<HTMLCanvasElement | null>(null);

	function draw() {
		const ctx = canvas?.getContext('2d');
		if (!ctx) return;
		ctx.clearRect(0, 0, W, H);
		const b = minimapBounds(graph);
		if (!b) return;
		const dims2d = { width: W, height: H, pad: PAD };

		// Nodes as faint dots.
		ctx.fillStyle = '#3b9de0';
		graph.forEachNode((_n, attrs) => {
			const p = projectToMinimap(attrs.x as number, attrs.y as number, b, dims2d);
			ctx.beginPath();
			ctx.arc(p.x, p.y, 1.4, 0, 2 * Math.PI);
			ctx.fill();
		});

		// Current viewport rectangle (the visible area of the main canvas in graph space).
		const dims = sigma.getDimensions();
		const tl = sigma.viewportToGraph({ x: 0, y: 0 });
		const br = sigma.viewportToGraph({ x: dims.width, y: dims.height });
		const a = projectToMinimap(tl.x, tl.y, b, dims2d);
		const c = projectToMinimap(br.x, br.y, b, dims2d);
		const rx = Math.min(a.x, c.x);
		const ry = Math.min(a.y, c.y);
		ctx.strokeStyle = '#e85a7a';
		ctx.lineWidth = 1;
		ctx.strokeRect(rx, ry, Math.abs(c.x - a.x), Math.abs(c.y - a.y));
	}

	let cameraOff: (() => void) | null = null;

	onMount(() => {
		const cam = sigma.getCamera();
		const onUpdate = () => draw();
		cam.on('updated', onUpdate);
		cameraOff = () => cam.off('updated', onUpdate);
		draw();
	});

	onDestroy(() => cameraOff?.());

	// Redraw when the parent signals a graph change.
	$effect(() => {
		void version;
		draw();
	});
</script>

<div
	class="pointer-events-none absolute bottom-3 left-3 z-10 overflow-hidden rounded-md border border-default bg-card/90 shadow-sm"
	style:width="{W}px"
	style:height="{H}px"
	aria-hidden="true"
>
	<canvas bind:this={canvas} width={W} height={H}></canvas>
</div>
