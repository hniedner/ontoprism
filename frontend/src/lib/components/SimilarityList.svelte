<script lang="ts">
	import type { ResolvedPathname } from '$app/types';

	interface Item {
		key: string;
		href: ResolvedPathname;
		label: string;
		score: number;
	}

	let { items }: { items: Item[] } = $props();
</script>

<ul class="flex flex-col gap-2">
	{#each items as item (item.key)}
		<li class="flex items-center justify-between gap-2 text-sm">
			<a href={item.href} class="min-w-0 truncate text-secondary no-underline hover:text-primary-600"
				>{item.label}</a
			>
			<span
				class="shrink-0 rounded bg-success-50 px-1.5 py-0.5 font-mono text-xs text-success tabular-nums dark:bg-success-900/30"
				>{item.score.toFixed(2)}</span
			>
		</li>
	{/each}
</ul>
