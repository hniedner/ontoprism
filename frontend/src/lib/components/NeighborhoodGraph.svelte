<script lang="ts">
	import type { EdgeKind, Neighborhood } from '$lib/types';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';

	const conceptHref = (code: string) => resolve('/repositories/ncit/[code]', { code });

	let { graph }: { graph: Neighborhood } = $props();

	const W = 720;
	const H = 500;
	const CX = W / 2;
	const CY = H / 2;
	const R = 185;

	const KIND_COLOR: Record<EdgeKind, string> = {
		subClassOf: '#94a3b8',
		role: '#3b9de0',
		association: '#42979a'
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

<div class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<div class="mb-2 flex items-center justify-between">
		<h3 class="text-sm font-semibold text-default">Concept neighborhood</h3>
		<span class="text-xs text-muted">{graph.nodes.length} nodes · {graph.edges.length} edges</span>
	</div>
	<div class="graph-surface overflow-hidden rounded-lg">
		<svg viewBox="0 0 {W} {H}" role="img" aria-label="Concept neighborhood graph">
			{#each graph.edges as edge (edge.source + edge.relation + edge.target)}
				{@const a = positions[edge.source]}
				{@const b = positions[edge.target]}
				{#if a && b}
					<line
						x1={a.x}
						y1={a.y}
						x2={b.x}
						y2={b.y}
						stroke={KIND_COLOR[edge.kind]}
						stroke-width="1.25"
						opacity="0.7"
					/>
					<text
						x={(a.x + b.x) / 2}
						y={(a.y + b.y) / 2}
						class="edge-label"
						fill={KIND_COLOR[edge.kind]}
					>
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
						<circle r={node.code === graph.center ? 30 : 21} />
						<text class="node-label">{node.code}</text>
						<title>{labelOf(node.code)}</title>
					</g>
				{/if}
			{/each}
		</svg>
	</div>

	<ul class="mt-3 flex flex-wrap gap-4 text-xs text-muted">
		<li class="flex items-center gap-1.5">
			<span class="swatch" style="background:{KIND_COLOR.subClassOf}"></span> subClassOf
		</li>
		<li class="flex items-center gap-1.5">
			<span class="swatch" style="background:{KIND_COLOR.role}"></span> role
		</li>
		<li class="flex items-center gap-1.5">
			<span class="swatch" style="background:{KIND_COLOR.association}"></span> association
		</li>
	</ul>
</div>

<style>
	.graph-surface {
		background: var(--nci-primary-50, #e8f4fc);
	}
	:global(.dark) .graph-surface {
		background: var(--color-neutral-900, #171717);
	}
	svg {
		width: 100%;
		height: auto;
		display: block;
	}
	.node circle {
		fill: #d1e9f9;
		stroke: #007bbd;
		stroke-width: 1.5;
		cursor: pointer;
		transition: fill 0.12s;
	}
	.node:hover circle {
		fill: #a3d3f3;
	}
	.node.center circle {
		fill: #006a9e;
		stroke: #14315c;
	}
	:global(.dark) .node circle {
		fill: #14315c;
		stroke: #2a9bd9;
	}
	:global(.dark) .node.center circle {
		fill: #007bbd;
		stroke: #5bb5e8;
	}
	.node.center .node-label {
		fill: #fff;
	}
	.node-label {
		font-size: 11px;
		font-weight: 600;
		text-anchor: middle;
		dominant-baseline: middle;
		pointer-events: none;
		fill: #0d2140;
	}
	:global(.dark) .node-label {
		fill: #d1e9f9;
	}
	.edge-label {
		font-size: 8.5px;
		text-anchor: middle;
		opacity: 0.85;
	}
	.swatch {
		width: 12px;
		height: 12px;
		border-radius: 3px;
		display: inline-block;
	}
</style>
