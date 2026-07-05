<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import favicon from '$lib/assets/favicon.svg';
	import { theme } from '$lib/stores/theme.svelte';
	import { buildBreadcrumbs } from '$lib/breadcrumbs';
	import { cn } from '$lib/utils/cn';

	let { children } = $props();

	const crumbs = $derived(buildBreadcrumbs(page.url.pathname));

	const nav = [
		{ href: resolve('/repositories/ncit'), path: '/repositories/ncit', label: 'NCIt' },
		{ href: resolve('/repositories/cadsr'), path: '/repositories/cadsr', label: 'caDSR' },
		{ href: resolve('/query'), path: '/query', label: 'SPARQL' },
		{ href: resolve('/refresh'), path: '/refresh', label: 'Refresh' }
	];

	function isActive(path: string): boolean {
		return page.url.pathname.startsWith(path);
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="flex min-h-screen flex-col bg-page-bg-subtle dark:bg-neutral-950">
	<!-- Branded header -->
	<header
		class="bg-gradient-to-r from-primary-800 to-primary-600 text-white shadow-md dark:from-neutral-900 dark:to-primary-900"
	>
		<div class="mx-auto flex items-center justify-between gap-4 px-[2%] py-3">
			<a href={resolve('/')} class="group flex items-center gap-3 no-underline">
				<span
					class="flex h-11 w-11 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25 backdrop-blur"
				>
					<svg viewBox="0 0 24 24" class="h-6 w-6" fill="none" aria-hidden="true">
						<path
							d="M12 2 3 7v10l9 5 9-5V7z"
							stroke="white"
							stroke-width="1.5"
							stroke-linejoin="round"
						/>
						<path
							d="M12 2v20M3 7l9 5 9-5M3 17l9-5 9 5"
							stroke="white"
							stroke-width="1.2"
							opacity="0.7"
						/>
					</svg>
				</span>
				<span class="leading-tight">
					<span class="block text-lg font-bold tracking-wide text-white">ONTOPRISM</span>
					<span class="block text-[0.7rem] font-medium text-white/70"
						>Ontology Explorer · NCIt &amp; caDSR</span
					>
				</span>
			</a>

			<nav class="flex items-center gap-1">
				{#each nav as item (item.path)}
					<a
						href={item.href}
						class={cn(
							'rounded-lg px-3 py-1.5 text-sm font-medium no-underline transition-colors',
							isActive(item.path)
								? 'bg-white/20 text-white'
								: 'text-white/80 hover:bg-white/10 hover:text-white'
						)}
					>
						{item.label}
					</a>
				{/each}
				<button
					type="button"
					onclick={() => theme.toggle()}
					class="ml-2 flex h-9 w-9 items-center justify-center rounded-lg text-white/80 transition-colors hover:bg-white/10 hover:text-white"
					aria-label="Toggle theme"
					title="Toggle light / dark"
				>
					{#if theme.current === 'dark'}
						<svg
							viewBox="0 0 24 24"
							class="h-5 w-5"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
						>
							<circle cx="12" cy="12" r="4" />
							<path
								d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
								stroke-linecap="round"
							/>
						</svg>
					{:else}
						<svg
							viewBox="0 0 24 24"
							class="h-5 w-5"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
						>
							<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" stroke-linejoin="round" />
						</svg>
					{/if}
				</button>
			</nav>
		</div>
	</header>

	<!-- Breadcrumbs -->
	<div class="border-b border-default bg-card">
		<nav
			class="mx-auto flex items-center gap-2 px-[2%] py-2.5 text-sm text-muted"
			aria-label="Breadcrumb"
		>
			{#each crumbs as crumb, i (crumb.href)}
				{#if i > 0}
					<span class="text-subtle" aria-hidden="true">›</span>
				{/if}
				{#if i === crumbs.length - 1}
					<span class="font-medium text-default">{crumb.label}</span>
				{:else}
					<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- breadcrumb hrefs are pre-built ancestor pathnames, not typed route ids -->
					<a href={crumb.href} class="text-muted no-underline hover:text-primary-600"
						>{crumb.label}</a
					>
				{/if}
			{/each}
		</nav>
	</div>

	<!-- Content -->
	<main class="mx-auto w-full flex-1 px-[2%] py-8">
		{@render children()}
	</main>

	<!-- Footer -->
	<footer class="border-t border-default bg-card">
		<div
			class="mx-auto flex flex-col items-center justify-between gap-1 px-[2%] py-4 text-xs text-muted sm:flex-row"
		>
			<span>ONTOPRISM · Ontology vertical slice</span>
			<span>NCIt &amp; caDSR terminology explorer</span>
		</div>
	</footer>
</div>
