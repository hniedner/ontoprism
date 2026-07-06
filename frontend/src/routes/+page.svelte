<script lang="ts">
	import { resolve } from '$app/paths';

	const repos = [
		{
			href: resolve('/repositories/ncit'),
			title: 'NCIt Concepts',
			blurb:
				'NCI Thesaurus — biomedical concepts with hierarchy, typed roles, neighborhood graphs, and semantic similarity.',
			accent: 'from-primary-500 to-primary-700'
		},
		{
			href: resolve('/repositories/cadsr'),
			title: 'caDSR CDEs',
			blurb:
				'Common Data Elements with NCIt concept mappings (ISO-11179 roles), permissible values, and similar CDEs.',
			accent: 'from-success-500 to-success-700'
		},
		{
			href: resolve('/repositories/clinicaltrials'),
			title: 'ClinicalTrials.gov',
			blurb:
				'Search the ClinicalTrials.gov v2 registry by condition — interventions, outcomes, eligibility, sponsors, and publication references.',
			accent: 'from-info-500 to-info-700'
		},
		{
			href: resolve('/repositories/pubmed'),
			title: 'PubMed',
			blurb:
				'Search the NCBI PubMed literature database — abstracts, authors, MeSH terms, and identifiers via the E-utilities API.',
			accent: 'from-warning-500 to-warning-700'
		}
	];
</script>

<svelte:head>
	<title>ONTOPRISM · Ontology Explorer</title>
</svelte:head>

<section class="mb-8">
	<h1 class="text-3xl font-semibold text-default">Ontology Explorer</h1>
	<p class="mt-2 max-w-2xl text-secondary">
		Search, browse, and cross-navigate the NCI Thesaurus and caDSR Common Data Elements — with
		graph visualization and embedding-based semantic similarity.
	</p>
</section>

<div class="grid gap-6 sm:grid-cols-2">
	{#each repos as repo (repo.href)}
		<a
			href={repo.href}
			class="group overflow-hidden rounded-xl border border-default bg-card shadow-sm no-underline transition-shadow hover:shadow-md"
		>
			<div class="h-1.5 bg-gradient-to-r {repo.accent}"></div>
			<div class="p-5">
				<h2
					class="flex items-center justify-between text-lg font-semibold text-default group-hover:text-primary-600"
				>
					{repo.title}
					<span class="text-muted transition-transform group-hover:translate-x-1">→</span>
				</h2>
				<p class="mt-2 text-sm text-muted">{repo.blurb}</p>
			</div>
		</a>
	{/each}
</div>
