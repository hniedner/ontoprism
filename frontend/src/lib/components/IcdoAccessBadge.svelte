<script lang="ts">
	import type { IcdoAccessStatus } from '$lib/types';

	let { status, compact = false }: { status: IcdoAccessStatus; compact?: boolean } = $props();
	const label = $derived(
		status === 'ready-and-entitled'
			? 'Ready and entitled'
			: status === 'entitlement-required'
				? 'Entitlement required'
				: 'Unavailable'
	);
	const shortLabel = $derived(
		status === 'ready-and-entitled' ? 'Entitled' : status === 'entitlement-required' ? 'Required' : 'Unavailable'
	);
	const tone = $derived(
		status === 'ready-and-entitled'
			? 'border-success-300 bg-success-50 text-success-800 dark:border-success-800 dark:bg-success-950 dark:text-success-200'
			: status === 'entitlement-required'
				? 'border-warning-300 bg-warning-50 text-warning-800 dark:border-warning-800 dark:bg-warning-950 dark:text-warning-200'
				: 'border-default bg-card text-muted'
	);
</script>

<span class={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`} aria-label={label}>
	{compact ? shortLabel : label}
</span>
