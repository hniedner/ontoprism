<script lang="ts">
	import type { PermissibleValue } from '$lib/types';

	let { values }: { values: PermissibleValue[] } = $props();
	const LIST_PREVIEW = 6;
	let expanded = $state(false);
	const visibleValues = $derived(expanded ? values : values.slice(0, LIST_PREVIEW));
</script>

<section class="min-w-0 rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 text-sm font-semibold text-default">Permissible values ({values.length})</h3>
	<ul class="flex flex-col gap-2 text-sm">
		{#each visibleValues as value (value.value + (value.meaning_code ?? ''))}
			<li>
				<strong class="text-default">{value.value}</strong>
				{#if value.meaning}<span class="text-muted"> — {value.meaning}</span>{/if}
			</li>
		{:else}
			<li class="italic text-subtle">Not an enumerated value domain.</li>
		{/each}
	</ul>
	{#if values.length > LIST_PREVIEW}
		<button
			type="button"
			aria-expanded={expanded}
			class="mt-3 text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400"
			onclick={() => (expanded = !expanded)}
		>
			{expanded ? 'Show fewer Permissible values' : `Show all ${values.length} Permissible values`}
		</button>
	{/if}
</section>
