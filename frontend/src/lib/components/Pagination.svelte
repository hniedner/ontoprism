<script lang="ts">
	interface Props {
		offset: number;
		limit: number;
		total: number;
		navigationTotal?: number;
		onPage: (offset: number) => void;
		onSize?: (size: 10 | 25 | 50 | 100) => void;
	}

	let { offset, limit, total, navigationTotal = total, onPage, onSize }: Props = $props();

	const from = $derived(total === 0 ? 0 : offset + 1);
	const to = $derived(Math.min(offset + limit, total));
	const page = $derived(Math.floor(offset / limit) + 1);
	const pages = $derived(Math.max(1, Math.ceil(navigationTotal / limit)));
	const canPrev = $derived(offset > 0);
	const canNext = $derived(offset + limit < navigationTotal);

	const btn =
		'flex h-8 w-8 items-center justify-center rounded-md border border-default bg-card text-secondary transition-colors enabled:hover:bg-subtle disabled:opacity-40 disabled:cursor-not-allowed';
</script>

<nav aria-label="Pagination" class="flex flex-wrap items-center justify-between gap-3 border-t border-default px-4 py-3 text-sm">
	<span class="text-muted">
		Showing <span class="font-medium text-default">{from.toLocaleString()}</span>–<span
			class="font-medium text-default">{to.toLocaleString()}</span
		>
		of <span class="font-medium text-default">{total.toLocaleString()}</span>
	</span>
		{#if onSize}<label class="text-muted">Rows per page <select aria-label="Rows per page" value={limit} onchange={(event) => onSize(Number(event.currentTarget.value) as 10 | 25 | 50 | 100)}>{#each [10, 25, 50, 100] as size (size)}<option value={size}>{size}</option>{/each}</select></label>{/if}
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
</nav>
