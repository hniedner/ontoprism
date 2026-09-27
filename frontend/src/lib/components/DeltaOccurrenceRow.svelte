<script lang="ts">
    import { resolve } from '$app/paths';
    import type { DeltaOccurrence } from '$lib/types';
    let { row }: { row: DeltaOccurrence } = $props();
    const represented = $derived(row.category === 'projected' || row.category === 'represented-through-collapse');
</script>

<li>
    <span>{row.role_code} some </span><a href={resolve('/repositories/ncit/[code]', { code: row.filler_code })}>{row.filler_code}</a>
    <p>Stated at <a href={resolve('/repositories/ncit/[code]', { code: row.anchor_code })}>{row.anchor_code}</a>; depth {row.depth}; path {row.structural_path.join('.')}</p>
    <p>{row.reason}</p>
    {#if represented && row.retained_filler}
        <p>Represented by <a href={resolve('/repositories/ncit/[code]', { code: row.retained_filler })}>{row.normalized_axis} / {row.retained_filler}</a></p>
    {/if}
    {#if row.category === 'unclassified'}<pre class="overflow-auto text-xs">{JSON.stringify(row, null, 2)}</pre>{/if}
</li>
