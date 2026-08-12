<script lang="ts">
	import type { RepositoryKind } from '$lib/repository-registry';

	let { kind, compact = false }: { kind: RepositoryKind; compact?: boolean } = $props();
	const local = $derived(kind === 'local-certified-proxy');
	const label = $derived(local ? 'Local certified proxy' : 'Remote live service');
</script>

<span
	class="inline-flex items-center gap-1.5 rounded-full border border-default bg-card px-2.5 py-0.5 text-xs font-medium text-secondary"
>
	<span class="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true"></span>
	<span aria-label={label}>{compact ? (local ? 'Local' : 'Live') : label}</span>
</span>
