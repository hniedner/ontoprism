<script lang="ts">
	import { resolve } from '$app/paths';
	import { similarCdes } from '$lib/api';
	import SimilarityPanel, { type SimilarityLink } from '$lib/components/SimilarityPanel.svelte';

	let { publicId }: { publicId: string } = $props();

	async function load(currentPublicId: string, signal: AbortSignal): Promise<SimilarityLink[]> {
		const items = await similarCdes(currentPublicId, 10, undefined, signal);
		return items.map((item) => ({
			key: `${item.public_id}:${item.version}`,
			href: resolve('/repositories/cadsr/[id]', { id: item.public_id }),
			label: item.long_name,
			score: item.score
		}));
	}
</script>

<SimilarityPanel requestKey={publicId} title="Similar CDEs" loadingLabel="Loading similar CDEs" {load} />
