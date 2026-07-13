<script lang="ts">
	import { getMappings } from '$lib/api';
	import type { ExternalMapping } from '$lib/types';

	let { code }: { code: string } = $props();

	let mappings = $state<ExternalMapping[]>([]);
	let loaded = $state(false);

	$effect(() => {
		loaded = false;
		getMappings(code).then(
			(m) => (mappings = m.mappings),
			() => (mappings = [])
		).finally(() => (loaded = true));
	});

	function badgeClass(lifecycle: string): string {
		switch (lifecycle) {
			case 'active': return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300';
			case 'validated': return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300';
			case 'proposed': return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300';
			case 'quarantined': return 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300';
			case 'retired': return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300';
			default: return 'bg-subtle text-muted';
		}
	}

	function predicateLabel(predicate: string): string {
		const short = predicate.split('/').pop() ?? predicate;
		return short.replace('Match', '');
	}
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		External Mappings
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loaded ? mappings.length : '…'}</span
		>
	</h3>
	{#if loaded && mappings.length === 0}
		<p class="text-sm italic text-subtle">No upstream mappings.</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each mappings as m (m.object_id + m.predicate)}
				<li class="flex flex-wrap items-baseline gap-1.5 text-sm">
					<span class="font-mono text-xs text-secondary">{m.object_id}</span>
					<span
						class="rounded px-1.5 py-0.5 text-xs font-medium {badgeClass(m.lifecycle)}"
					>{predicateLabel(m.predicate)}</span
					>
					{#if m.confidence > 0}
						<span class="text-xs text-muted">{Math.round(m.confidence * 100)}%</span>
					{/if}
					{#if m.is_identity}
						<span class="text-xs text-green-600 dark:text-green-400">identity</span>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</section>
