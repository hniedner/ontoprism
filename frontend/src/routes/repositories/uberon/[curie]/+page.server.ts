import { getUberonConcept, getUberonNeighborhood } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { Neighborhood } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const [detail, rawGraph] = await critical(
		Promise.all([
			getUberonConcept(params.curie, fetch),
			getUberonNeighborhood(params.curie, 1, fetch)
		])
	);
	const graph: Neighborhood = {
		...rawGraph,
		nodes: rawGraph.nodes.map((node) => ({
			...node,
			semantic_type: node.source === 'cl' ? 'Cell Ontology' : 'Uberon',
			representation_status: null
		})),
		edges: rawGraph.edges.map((edge) => ({
			...edge,
			kind: edge.kind === 'subClassOf' ? 'subClassOf' : 'role'
		}))
	};
	return { detail, graph };
};
