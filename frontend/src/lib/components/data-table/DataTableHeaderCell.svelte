<script lang="ts" generics="Row">
	import DataTableFilterPopover from './DataTableFilterPopover.svelte';
	import type { DataTableColumn, DataTableFilterState, DataTableIntent, DataTableSortState } from './types';
	let { column, sort, filterValue, filterOpen, className, style, onintent, ontogglefilter, onclosefilter }: {
		column: DataTableColumn<Row>; sort: DataTableSortState | null;
		filterValue: DataTableFilterState | undefined; filterOpen: boolean;
		className: string; style?: string; onintent: (intent: DataTableIntent) => void;
		ontogglefilter: (columnId: string) => void; onclosefilter: (columnId: string) => void;
	} = $props();
	let trigger = $state<HTMLButtonElement>();
	let popover = $state<HTMLDivElement>();
	const sortable = $derived(Boolean(column.sortable?.length));
	const activeDirection = $derived(sort?.key === column.id ? sort.direction : null);
	const ariaSort = $derived(activeDirection === null ? (sortable ? 'none' : undefined) : activeDirection === 'asc' ? 'ascending' : 'descending');
	const indicator = $derived(activeDirection === 'asc' ? '↑' : activeDirection === 'desc' ? '↓' : column.sortable?.length === 1 ? (column.sortable[0] === 'asc' ? '↑' : '↓') : '↕');
	const selected = $derived(filterValue?.selected ?? []);
	const selectedLabels = $derived(column.filter?.options.filter((option) => selected.includes(option.value)).map((option) => option.label) ?? []);
	const filterLabel = $derived(selectedLabels.length
		? `Filter ${column.label}, ${selectedLabels.length} selected: ${selectedLabels.join(', ')}`
		: `Filter ${column.label}`);
	let popoverStyle = $state('');
	function nextSort(): void {
		const directions = column.sortable ?? [];
		const current = sort?.key === column.id ? directions.indexOf(sort.direction) : -1;
		if (directions.length === 1 && current === 0) {
			onintent({ kind: 'reset' });
			return;
		}
		const direction = directions[current < 0 ? 0 : (current + 1) % directions.length];
		if (direction) onintent({ kind: 'sort', sort: { key: column.id, direction } });
	}
	function positionPopover(): void {
		if (!trigger) return;
		const bounds = trigger.getBoundingClientRect();
		const width = Math.min(288, window.innerWidth - 16);
		const left = Math.max(8, Math.min(bounds.left, window.innerWidth - width - 8));
		const availableBelow = Math.max(80, window.innerHeight - bounds.bottom - 8);
		const availableAbove = Math.max(80, bounds.top - 8);
		const contentHeight = popover?.scrollHeight ?? 320;
		if (availableBelow < Math.min(contentHeight, 320) && availableAbove > availableBelow) {
			popoverStyle = `bottom: ${window.innerHeight - bounds.top + 4}px; left: ${left}px; width: ${width}px; max-height: ${availableAbove}px`;
			return;
		}
		popoverStyle = `top: ${bounds.bottom + 4}px; left: ${left}px; width: ${width}px; max-height: ${availableBelow}px`;
	}
	function closeFilter(restoreFocus: boolean): void {
		onclosefilter(column.id);
		if (restoreFocus) queueMicrotask(() => trigger?.focus());
	}
	$effect(() => {
		if (!filterOpen) return;
		positionPopover();
		function handlePointerDown(event: PointerEvent): void {
			const target = event.target;
			if (!(target instanceof Element) || trigger?.contains(target) || popover?.contains(target)) return;
			closeFilter(target.closest('[data-datatable-filter-trigger]') === null);
		}
		function handleKeydown(event: KeyboardEvent): void {
			if (event.key !== 'Escape') return;
			event.preventDefault();
			closeFilter(true);
		}
		document.addEventListener('pointerdown', handlePointerDown);
		document.addEventListener('keydown', handleKeydown);
		window.addEventListener('resize', positionPopover);
		window.addEventListener('scroll', positionPopover, true);
		return () => {
			document.removeEventListener('pointerdown', handlePointerDown);
			document.removeEventListener('keydown', handleKeydown);
			window.removeEventListener('resize', positionPopover);
			window.removeEventListener('scroll', positionPopover, true);
		};
	});
</script>
<th scope="col" class={className} {style} aria-sort={ariaSort}>
	<div class="flex items-center gap-1">
		{#if sortable}
			<button type="button" class="inline-flex items-center gap-1 hover:text-default" aria-label={`Sort by ${column.label}`} onclick={nextSort}>{column.label} <span aria-hidden="true" class="text-subtle">{indicator}</span></button>
		{:else}
			<span>{column.label}</span>
		{/if}
		{#if column.filter}
			<button
				bind:this={trigger}
				type="button"
				data-datatable-filter-trigger
				class={`relative inline-flex min-h-7 min-w-7 items-center justify-center rounded border px-1.5 ${selected.length ? 'border-accent bg-accent/10 text-accent' : 'border-transparent text-muted hover:border-default hover:text-default'}`}
				aria-label={filterLabel}
				aria-expanded={filterOpen}
				aria-haspopup="dialog"
				onclick={() => ontogglefilter(column.id)}
			>
				<svg aria-hidden="true" viewBox="0 0 20 20" class="h-3.5 w-3.5 fill-current"><path d="M3 4h14l-5.5 6.2V15l-3 1.5v-6.3z" /></svg>
				{#if selected.length}<span aria-hidden="true" class="ml-1 text-[10px] font-bold">{selected.length}</span>{/if}
			</button>
			{#if filterOpen}
				<div bind:this={popover} class="fixed z-50 overflow-y-auto" style={popoverStyle}>
					<DataTableFilterPopover columnId={column.id} columnLabel={column.label} filter={column.filter} value={filterValue} {onintent} />
				</div>
			{/if}
		{/if}
	</div>
</th>
