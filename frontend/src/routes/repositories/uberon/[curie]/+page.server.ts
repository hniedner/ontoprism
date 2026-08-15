import { getUberonAlignments, getUberonConcept, getUberonNeighborhood } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { Neighborhood } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const [repository, alignments] = await critical(
		Promise.all([
			(async () => {
				const detail = await getUberonConcept(params.curie, fetch);
				const rawGraph = await getUberonNeighborhood(params.curie, 1, fetch);
				return { detail, rawGraph };
			})(),
			getUberonAlignments(params.curie, fetch)
		])
	);
	const { detail, rawGraph } = repository;
	const graph: Neighborhood = {
		...rawGraph,
		nodes: rawGraph.nodes.map((node) => ({
			...node,
			semantic_type: node.source === 'cl' ? 'Cell Ontology' : 'Uberon',
			representation_status: null
		}))
	};
	return { detail, graph, alignments };
};
