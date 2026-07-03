<script lang="ts">
	import type { EdgeKind, Neighborhood } from '$lib/types';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';

	const conceptHref = (code: string) => resolve('/repositories/ncit/[code]', { code });

	let { graph }: { graph: Neighborhood } = $props();

	const W = 680;
	const H = 480;
	const CX = W / 2;
	const CY = H / 2;
	const R = 175;

	const KIND_COLOR: Record<EdgeKind, string> = {
		subClassOf: '#6b7280',
		role: '#2563eb',
		association: '#16a34a'
	};

	// Radial layout: center node in the middle, everything else on a circle.
	let positions = $derived.by(() => {
		const others = graph.nodes.filter((n) => n.code !== graph.center);
		const pos: Record<string, { x: number; y: number }> = {
			[graph.center]: { x: CX, y: CY }
		};
		others.forEach((n, i) => {
			const angle = (i / Math.max(others.length, 1)) * 2 * Math.PI - Math.PI / 2;
			pos[n.code] = { x: CX + R * Math.cos(angle), y: CY + R * Math.sin(angle) };
		});
		return pos;
	});

	const labelOf = (code: string) => graph.nodes.find((n) => n.code === code)?.label ?? code;
</script>

<div class="wrap">
	<svg viewBox="0 0 {W} {H}" role="img" aria-label="Concept neighborhood graph">
		{#each graph.edges as edge (edge.source + edge.relation + edge.target)}
			{@const a = positions[edge.source]}
			{@const b = positions[edge.target]}
			{#if a && b}
				<line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={KIND_COLOR[edge.kind]} stroke-width="1.5" />
				<text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2} class="edge-label" fill={KIND_COLOR[edge.kind]}>
					{edge.relation_label ?? edge.relation}
				</text>
			{/if}
		{/each}

		{#each graph.nodes as node (node.code)}
			{@const p = positions[node.code]}
			{#if p}
				<g
					class="node"
					class:center={node.code === graph.center}
					transform="translate({p.x},{p.y})"
					role="button"
					tabindex="0"
					onclick={() => goto(conceptHref(node.code))}
					onkeydown={(e) => e.key === 'Enter' && goto(conceptHref(node.code))}
				>
					<circle r={node.code === graph.center ? 28 : 20} />
					<text class="node-label">{node.code}</text>
					<title>{labelOf(node.code)}</title>
				</g>
			{/if}
		{/each}
	</svg>

	<ul class="legend">
		<li><span class="swatch" style="background:{KIND_COLOR.subClassOf}"></span> subClassOf</li>
		<li><span class="swatch" style="background:{KIND_COLOR.role}"></span> role</li>
		<li><span class="swatch" style="background:{KIND_COLOR.association}"></span> association</li>
	</ul>
</div>

<style>
	.wrap {
		border: 1px solid #e2e2e2;
		border-radius: 8px;
		padding: 0.5rem;
	}
	svg {
		width: 100%;
		height: auto;
	}
	.node circle {
		fill: #dbeafe;
		stroke: #2563eb;
		stroke-width: 1.5;
		cursor: pointer;
	}
	.node.center circle {
		fill: #2563eb;
	}
	.node.center .node-label {
		fill: #fff;
	}
	.node-label {
		font-size: 11px;
		text-anchor: middle;
		dominant-baseline: middle;
		pointer-events: none;
	}
	.edge-label {
		font-size: 9px;
		text-anchor: middle;
	}
	.legend {
		display: flex;
		gap: 1rem;
		list-style: none;
		padding: 0.4rem 0 0;
		margin: 0;
		font-size: 0.8rem;
	}
	.legend li {
		display: flex;
		align-items: center;
		gap: 0.3rem;
	}
	.swatch {
		width: 12px;
		height: 12px;
		border-radius: 2px;
		display: inline-block;
	}
</style>
