<script lang="ts">
	import { resolve } from '$app/paths';
	import RepositoryKindBadge from '$lib/components/RepositoryKindBadge.svelte';
	import { repositories, type RepositoryId } from '$lib/repository-registry';

	const presentation: Record<RepositoryId, { title: string; blurb: string; accent: string }> = {
		ncit: {
			title: 'NCIt Concepts',
			blurb:
				'NCI Thesaurus — biomedical concepts with hierarchy, typed roles, neighborhood graphs, and semantic similarity.',
			accent: 'from-primary-500 to-primary-700'
		},
		cadsr: {
			title: 'caDSR CDEs',
			blurb:
				'Common Data Elements with NCIt concept mappings (ISO-11179 roles), permissible values, and similar CDEs.',
			accent: 'from-success-500 to-success-700'
		},
		uberon: {
			title: 'Uberon/CL Concepts',
			blurb:
				'Anatomy and cell concepts from the certified combined Uberon and Cell Ontology index, with hierarchy and OWL restrictions.',
			accent: 'from-violet-500 to-violet-700'
		},
		icdo: {
			title: 'ICD-O Datasets',
			blurb: 'Entitlement-gated, certified ICD-O-3.2 and ICD-O-4 morphology and topography datasets.',
			accent: 'from-rose-500 to-rose-700'
		},
		clinicaltrials: {
			title: 'ClinicalTrials.gov',
			blurb:
				'Search the ClinicalTrials.gov v2 registry by condition — interventions, outcomes, eligibility, sponsors, and publication references.',
			accent: 'from-info-500 to-info-700'
		},
		pubmed: {
			title: 'PubMed',
			blurb:
				'Search the NCBI PubMed literature database — abstracts, authors, MeSH terms, and identifiers via the E-utilities API.',
			accent: 'from-warning-500 to-warning-700'
		}
	};

	const repos = repositories.map((entry) => ({ ...entry, ...presentation[entry.id] }));

	function href(path: (typeof repositories)[number]['path']): string {
		return path === '/repositories/ncit'
			? resolve('/repositories/ncit')
			: path === '/repositories/cadsr'
				? resolve('/repositories/cadsr')
				: path === '/repositories/uberon'
					? resolve('/repositories/uberon')
					: path === '/repositories/icdo'
						? resolve('/repositories/icdo')
					: path === '/repositories/clinicaltrials'
						? resolve('/repositories/clinicaltrials')
						: resolve('/repositories/pubmed');
	}
</script>

<svelte:head>
	<title>ONTOPRISM · Ontology Explorer</title>
</svelte:head>

<section class="mb-8">
	<h1 class="text-3xl font-semibold text-default">Current ontology capabilities</h1>
	<p class="mt-2 max-w-2xl text-secondary">
		Search, browse, and cross-navigate certified NCIt, caDSR, Uberon/CL, and ICD-O
		repositories, alongside live clinical-trial and literature services.
	</p>
</section>

<div class="grid gap-6 sm:grid-cols-2">
	{#each repos as repo (repo.id)}
		<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- href() resolves every validated manifest path through a typed route branch -->
		<a href={href(repo.path)}
			class="group overflow-hidden rounded-xl border border-default bg-card shadow-sm no-underline transition-shadow hover:shadow-md"
		>
			<div class="h-1.5 bg-gradient-to-r {repo.accent}"></div>
			<div class="p-5">
				<div class="flex items-start justify-between gap-3">
					<h2 class="text-lg font-semibold text-default group-hover:text-primary-600">{repo.title}</h2>
					<span class="text-muted transition-transform group-hover:translate-x-1">→</span>
				</div>
				<div class="mt-2"><RepositoryKindBadge kind={repo.kind} /></div>
				<p class="mt-2 text-sm text-muted">{repo.blurb}</p>
			</div>
		</a>
	{/each}
</div>
