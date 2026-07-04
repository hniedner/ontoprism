<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Relationship } from '$lib/types';

	let { title, items }: { title: string; items: Relationship[] } = $props();
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		{title}
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted">{items.length}</span>
	</h3>
	{#if items.length === 0}
		<p class="text-sm italic text-subtle">None.</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each items as rel (rel.relation + rel.target.code)}
				<li class="flex flex-wrap items-baseline gap-1.5 text-sm">
					<span class="font-mono text-xs text-primary-600 dark:text-primary-400"
						>{rel.relation_label ?? rel.relation}</span
					>
					<span class="text-subtle">→</span>
					<a
						href={resolve('/repositories/ncit/[code]', { code: rel.target.code })}
						class="text-secondary no-underline hover:text-primary-600">{rel.target.label ?? rel.target.code}</a
					>
					<span class="font-mono text-xs text-subtle">{rel.target.code}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>
