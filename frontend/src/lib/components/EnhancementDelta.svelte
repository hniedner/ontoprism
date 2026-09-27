<script lang="ts">
    import { getEnhancementDelta } from '$lib/api';
    import type { DeltaCategory, DeltaOccurrence } from '$lib/types';
    import { handleLatest } from '$lib/latest';
    import LoadingState from './LoadingState.svelte';
    import DeltaOccurrenceRow from './DeltaOccurrenceRow.svelte';
    let { runId, code }: { runId: string; code: string } = $props();
    let rows = $state<DeltaOccurrence[] | null>(null);
    let failed = $state(false);
    const categories: [DeltaCategory, string][] = [
        ['projected', 'Projected'], ['represented-through-collapse', 'Represented through collapse'],
        ['not-projected', 'Not projected'], ['not-considered', 'Not considered by the decomposition'],
        ['unclassified', 'Unclassified']
    ];
    $effect(() => {
        rows = null;
        failed = false;
        const controller = new AbortController();
        return handleLatest(getEnhancementDelta(runId, code, controller.signal), {
            ready: result => rows = result,
            failed: () => failed = true,
            settled: () => {}
        }, () => controller.abort());
    });
</script>

<section aria-label="Stated occurrence delta" class="mt-4 border-t border-default pt-3 text-sm">
    <h4 class="font-semibold">Stated occurrence delta</h4>
    <p class="text-muted">All stored stated roles, including inherited occurrences. Official NCIt remains unchanged.</p>
    {#if failed}
        <p role="alert">Stated occurrence delta unavailable; counts are not known.</p>
    {:else if rows === null}
        <LoadingState active label="Loading stated occurrence delta" minHeight="4rem" />
    {:else if rows.length === 0}
        <p>No stated role occurrences were recorded for this concept in this run. The delta cannot establish whether NCIt has no stated roles; see the concept outcome above.</p>
    {:else}
        {#if !rows.some(row => row.category === 'not-projected')}
            <p>No stated role occurrences are classified as not projected.</p>
        {/if}
        {#each categories as [category, label] (category)}
            {@const items = rows.filter(row => row.category === category)}
            <details class="mt-2" open={category === 'not-projected' || category === 'unclassified' && items.length > 0}>
                <summary class="cursor-pointer font-medium">{label}: {items.length}</summary>
                <ul class="space-y-3 pl-3">
                    {#each items as row (row.occurrence_id)}
                        <DeltaOccurrenceRow {row} />
                    {/each}
                </ul>
            </details>
        {/each}
    {/if}
</section>
