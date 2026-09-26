<script lang="ts">
    import type { DecompositionConstituent } from '$lib/types';
    import ReviewBadge from './ReviewBadge.svelte';
    let { constituent: c, publicationStatus }: { constituent: DecompositionConstituent; publicationStatus: 'provisional' | null } = $props();
</script>

{#if c.most_specific}
    <span class="shrink-0 rounded bg-subtle px-1.5 py-0.5 text-xs text-muted" title="Chosen as the most-specific filler over its ancestors">leaf</span>
{/if}
{#if c.source_group_ids.length}
    <span class="text-xs text-subtle">Source groups: {c.source_group_ids.join(', ')}</span>
{/if}
{#if c.axis_ambiguous}<span class="text-xs text-subtle">Ambiguous axis</span>{/if}
<ReviewBadge visible={c.needs_review} />
{#if publicationStatus}<span class="text-xs">{publicationStatus}</span>{/if}
{#if c.filler.startsWith('MINT-')}<span class="text-xs">mint-filler: proposed filler</span>{/if}
