<script lang="ts">
    import type { ConstituentEvidence } from '$lib/types';
    import LoadingState from './LoadingState.svelte';
    let { evidence, failed, runId }: { evidence: ConstituentEvidence[] | null; failed: boolean; runId: string | null } = $props();
    const kinds = ['restriction-backed', 'genus-backed', 'not-source-backed'] as const;
</script>

{#if evidence}
    <div class="mb-3 text-sm">
        <h4 class="font-semibold">Source-backed filler share</h4>
        <p>The filler is literally stated in NCIt; this does not mean the decomposition is accepted or correct.</p>
        {#each kinds as kind (kind)}
            {@const count = evidence.filter(row => row.support === kind).length}
            <p>{kind}: {count}/{evidence.length} ({(100 * count / evidence.length).toFixed(1)}%)</p>
        {/each}
    </div>
{:else if failed}
    <p role="alert">Source evidence unavailable. Filler support is not known.</p>
{:else if runId}
    <LoadingState active label="Loading source evidence" minHeight="2rem" />
{:else}
    <p>Source evidence unavailable: no published run identifier.</p>
{/if}
