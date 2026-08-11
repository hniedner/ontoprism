<script lang="ts">
	import { resolve } from '$app/paths';
	import { similarConcepts } from '$lib/api';
	import SimilarityPanel, { type SimilarityLink } from '$lib/components/SimilarityPanel.svelte';

	let { code }: { code: string } = $props();

	async function load(currentCode: string, signal: AbortSignal): Promise<SimilarityLink[]> {
		const items = await similarConcepts(currentCode, 10, undefined, signal);
		return items.map((item) => ({
			key: item.code,
			href: resolve('/repositories/ncit/[code]', { code: item.code }),
			label: item.label ?? item.code,
			score: item.score
		}));
	}
</script>

<SimilarityPanel requestKey={code} title="Similar concepts" loadingLabel="Loading similar concepts" {load} />
