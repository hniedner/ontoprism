<script lang="ts">
	let { service }: { service: 'NCBI PubMed' | 'ClinicalTrials.gov' } = $props();
</script>

<aside
	role="note"
	class="mb-4 rounded-lg border border-info-200 bg-info-50 px-4 py-3 text-sm text-secondary dark:border-info-800 dark:bg-info-900/20"
>
	<strong>{service} is queried live.</strong>
	Results are not reproducible from certified local state. Query and request data are sent to
	{service}.
</aside>
