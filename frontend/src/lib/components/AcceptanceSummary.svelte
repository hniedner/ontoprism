<script lang="ts">
	import { resolve } from '$app/paths';
	import type { AcceptanceProjection } from '$lib/types';

	type AcceptedProjection = Exclude<AcceptanceProjection, { status: 'not-accepted' }>;

	let { acceptance }: { acceptance: AcceptedProjection } = $props();

	const label = $derived.by(() => {
		switch (acceptance.status) {
			case 'projected':
				return 'Projected — Machine evidence accepted';
			case 'review-required-excluded':
				return 'Review required — excluded';
			case 'unknown-withheld':
				return 'Unknown outcome — withheld';
			case 'residual-withheld':
				return 'Residual classification — withheld';
			case 'withheld-evidence-gap':
				return 'Evidence gap — withheld';
		}
	});
</script>

<div class="mb-3 space-y-1 text-sm">
	<p class="font-semibold text-amber-700 dark:text-amber-300">{label}</p>
	<p class="text-subtle">
		Basis: {acceptance.acceptance_basis}; run {acceptance.run_id}; representation
		{acceptance.representation_identity.slice(0, 8)}; publication
		{acceptance.publication_identity.slice(0, 8)}.
	</p>
	<a class="underline" href={resolve(acceptance.official_source_url)}>
		Official NCIt source {acceptance.source_release}
	</a>
</div>

{#if acceptance.status === 'review-required-excluded'}
	<div class="mb-3 space-y-1 text-sm">
		<p class="font-semibold text-amber-700 dark:text-amber-300">
			{acceptance.exclusion_summary}
		</p>
		<p class="text-subtle">The source assertion remains retrievable.</p>
	</div>
{/if}
