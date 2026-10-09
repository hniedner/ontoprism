<script lang="ts">
    import type { ConstituentEvidence } from '$lib/types';
    import LoadingState from './LoadingState.svelte';
    let { evidence, failed, runId }: { evidence: ConstituentEvidence[] | null; failed: boolean; runId: string | null } = $props();
    const kinds = Object.keys({ 'restriction-backed': true, 'genus-backed': true,
        'p334-backed': true, 'not-source-backed': true } satisfies Record<ConstituentEvidence['support'], true>);
</script>

{#if evidence}
    <div class="mb-3 text-sm">
        <h4 class="font-semibold">Source-backed filler share</h4>
        <p>Support is an exact stated restriction/genus or corroborated P334 anchor evidence; it does not establish acceptance or correctness.</p>
        {#each kinds as kind (kind)}
            {@const count = evidence.filter(row => row.support === kind).length}
            <p>{`${kind}: ${count}/${evidence.length} (${(100 * count / evidence.length).toFixed(1)}%)`}</p>
        {/each}
    </div>
{:else if failed}
    <p role="alert">Source evidence unavailable. Filler support is not known.</p>
{:else if runId}
    <LoadingState active label="Loading source evidence" minHeight="2rem" />
{:else}
    <p>Source evidence unavailable: no published run identifier.</p>
{/if}
