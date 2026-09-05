<script lang="ts">
	let { count, total, hasPrevious, hasNext, size, onPrevious, onNext, onSize }: { count: number; total: number; hasPrevious: boolean; hasNext: boolean; size: 10 | 25 | 50 | 100; onPrevious: () => void; onNext: () => void; onSize: (size: 10 | 25 | 50 | 100) => void } = $props();
</script>
<nav aria-label="Pagination" class="flex flex-wrap items-center justify-between gap-3 border-t border-default px-4 py-3 text-sm">
	<span aria-live="polite">Showing {count.toLocaleString()} on this page of {total.toLocaleString()} total</span>
	<label>Rows per page <select aria-label="Rows per page" value={size} onchange={(event) => onSize(Number(event.currentTarget.value) as 10 | 25 | 50 | 100)}>{#each [10,25,50,100] as option (option)}<option value={option}>{option}</option>{/each}</select></label>
	<div class="flex items-center gap-2"><button type="button" disabled={!hasPrevious} onclick={onPrevious}>Previous page</button><button type="button" disabled={!hasNext} onclick={onNext}>Next page</button></div>
</nav>
