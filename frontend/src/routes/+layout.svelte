<script lang="ts">
	import { resolve } from '$app/paths';
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<header class="topbar">
	<a href={resolve('/')} class="brand">ONTOPRISM</a>
	<nav>
		<a href={resolve('/repositories/ncit')}>NCIt</a>
		<a href={resolve('/repositories/cadsr')}>caDSR</a>
	</nav>
</header>

<main>
	{@render children()}
</main>

<style>
	:global(body) {
		margin: 0;
		font-family:
			system-ui,
			-apple-system,
			sans-serif;
		color: #1e293b;
	}
	.topbar {
		display: flex;
		align-items: center;
		gap: 1.5rem;
		padding: 0.75rem 1.5rem;
		border-bottom: 1px solid #e2e8f0;
		background: #f8fafc;
	}
	.brand {
		font-weight: 700;
		text-decoration: none;
		color: #0f172a;
	}
	nav a {
		color: #2563eb;
		text-decoration: none;
	}
	main {
		max-width: 960px;
		margin: 0 auto;
		padding: 1.5rem;
	}
</style>
