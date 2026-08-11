<script lang="ts">
	interface Props {
		active: boolean;
		label: string;
		delayMs?: number;
		minHeight?: string;
	}

	let { active, label, delayMs = 150, minHeight = '4rem' }: Props = $props();
	let delayedVisible = $state(false);
	const visible = $derived(delayMs === 0 ? active : delayedVisible);

	$effect(() => {
		if (!active) {
			delayedVisible = false;
			return;
		}
		if (delayMs === 0) {
			delayedVisible = false;
			return;
		}
		const timer = window.setTimeout(() => {
			delayedVisible = true;
		}, delayMs);
		return () => window.clearTimeout(timer);
	});
</script>

<div
	class="flex items-center justify-center rounded-xl"
	style:min-height={minHeight}
	aria-busy={active}
>
	{#if visible}
		<div role="status" aria-live="polite" class="flex items-center gap-2 text-sm text-muted">
			<svg
				class="h-4 w-4 animate-spin"
				viewBox="0 0 24 24"
				fill="none"
				aria-hidden="true"
			>
				<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
				<path
					class="opacity-75"
					fill="currentColor"
					d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4Z"
				/>
			</svg>
			<span>{label}</span>
		</div>
	{/if}
</div>
