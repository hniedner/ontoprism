<script lang="ts">
	import type { RepresentationStatus } from '$lib/types';

	let { status }: { status: RepresentationStatus } = $props();
	const labels: Record<RepresentationStatus, string> = {
		'legacy-precoordinated': 'Legacy pre-coordinated'
	};
</script>

<span
	class="inline-flex whitespace-nowrap rounded-full border border-amber-500/60 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
>
	{labels[status]}
</span>
