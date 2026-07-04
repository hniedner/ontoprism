<script lang="ts">
	interface Props {
		offset: number;
		limit: number;
		total: number;
		onPage: (offset: number) => void;
	}

	let { offset, limit, total, onPage }: Props = $props();

	const from = $derived(total === 0 ? 0 : offset + 1);
	const to = $derived(Math.min(offset + limit, total));
	const page = $derived(Math.floor(offset / limit) + 1);
	const pages = $derived(Math.max(1, Math.ceil(total / limit)));
	const canPrev = $derived(offset > 0);
	const canNext = $derived(offset + limit < total);

	const btn =
		'flex h-8 w-8 items-center justify-center rounded-md border border-default bg-card text-secondary transition-colors enabled:hover:bg-subtle disabled:opacity-40 disabled:cursor-not-allowed';
</script>

<div class="flex flex-wrap items-center justify-between gap-3 border-t border-default px-4 py-3 text-sm">
	<span class="text-muted">
		Showing <span class="font-medium text-default">{from.toLocaleString()}</span>–<span
			class="font-medium text-default">{to.toLocaleString()}</span
		>
		of <span class="font-medium text-default">{total.toLocaleString()}</span>
	</span>
	<div class="flex items-center gap-2">
		<button type="button" class={btn} disabled={!canPrev} onclick={() => onPage(0)} aria-label="First page">«</button>
		<button
			type="button"
			class={btn}
			disabled={!canPrev}
			onclick={() => onPage(Math.max(0, offset - limit))}
			aria-label="Previous page">‹</button
		>
		<span class="px-2 text-muted">Page {page.toLocaleString()} of {pages.toLocaleString()}</span>
		<button
			type="button"
			class={btn}
			disabled={!canNext}
			onclick={() => onPage(offset + limit)}
			aria-label="Next page">›</button
		>
		<button
			type="button"
			class={btn}
			disabled={!canNext}
			onclick={() => onPage((pages - 1) * limit)}
			aria-label="Last page">»</button
		>
	</div>
</div>
