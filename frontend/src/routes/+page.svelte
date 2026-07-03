<script lang="ts">
	import { resolve } from '$app/paths';
</script>

<h1>ONTOPRISM</h1>
<p>Ontology storage, query, and graph visualization.</p>

<ul class="repos">
	<li><a href={resolve('/repositories/ncit')}>NCIt repository →</a></li>
	<li><a href={resolve('/repositories/cadsr')}>caDSR repository →</a></li>
</ul>

<style>
	.repos {
		list-style: none;
		padding: 0;
		font-size: 1.1rem;
	}
	.repos a {
		color: #2563eb;
	}
</style>
