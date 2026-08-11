<script lang="ts">
	import { page } from '$app/state';
</script>

<svelte:head>
	<title>{page.status} · ONTOPRISM</title>
</svelte:head>

<section
	role="alert"
	class="rounded-xl border border-danger-200 bg-danger-50 p-6 text-danger dark:border-danger-800 dark:bg-danger-900/20"
>
	<p class="text-sm font-semibold">Request failed ({page.status})</p>
	<h1 class="mt-2 text-xl font-semibold">{page.error?.message ?? 'Unable to load this page'}</h1>
	<p class="mt-3 text-sm">
		Check the address or try again. If the problem continues, review the application service status.
	</p>
</section>
