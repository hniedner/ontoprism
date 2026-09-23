<script lang="ts">
	let { visible }: { visible: boolean } = $props();
</script>

{#if visible}
	<span class="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900">Needs review</span>
{/if}
