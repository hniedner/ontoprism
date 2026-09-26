<script lang="ts">
    import { resolve } from '$app/paths';
    import type { ConstituentEvidence } from '$lib/types';
    let { evidence }: { evidence: ConstituentEvidence } = $props();
</script>

<details class="w-full rounded border border-default p-2 text-xs">
    <summary class="cursor-pointer font-medium">{evidence.support} — source evidence</summary>
    {#each evidence.sources as source (source.fact_id + (source.occurrence_id ?? ''))}
        <div class="mt-2 break-words">
            <a class="text-secondary underline" href={resolve('/repositories/ncit/[code]', { code: source.anchor_code })}>NCIt definition {source.anchor_code}</a>
            <p>{source.kind === 'restriction' ? `${source.role_code} some ${source.filler_code}` : `Stated genus ${source.filler_code}`}</p>
            <p>Source depth: {source.depth}; group: {source.group_id}</p>
            <p>Fact: {source.fact_id}</p>
            {#if source.occurrence_id}<p>Occurrence: {source.occurrence_id}; path: {source.structural_path.join(' / ')}</p>{/if}
        </div>
    {:else}
        <p class="mt-2">No exact linked stated filler source.</p>
    {/each}
    {#if evidence.policy_choices.length}<p class="mt-2">Policy choices needing corroboration: {evidence.policy_choices.join(', ')}</p>{/if}
    {#each evidence.inferred_assertions as reason (reason)}<p class="mt-2">{reason}</p>{/each}
</details>
