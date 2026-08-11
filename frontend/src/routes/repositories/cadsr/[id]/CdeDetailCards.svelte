<script lang="ts">
	import SimilarCdes from '$lib/components/SimilarCdes.svelte';
	import type { CdeDetail } from '$lib/types';
	import CdeConceptsCard from './CdeConceptsCard.svelte';
	import CdePermissibleValuesCard from './CdePermissibleValuesCard.svelte';

	let { cde }: { cde: CdeDetail } = $props();
</script>

<div class="grid items-start gap-6 md:grid-cols-2 lg:grid-cols-3">
	<CdeConceptsCard concepts={cde.concepts} />
	<CdePermissibleValuesCard values={cde.permissible_values} />
	<SimilarCdes publicId={cde.public_id} />
</div>
