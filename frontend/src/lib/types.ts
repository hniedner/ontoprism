// Types mirroring the backend NCIt read models (fairlib.terminologies.ncit.models).

export interface ConceptRef {
	code: string;
	label: string | null;
}

export interface Relationship {
	relation: string;
	relation_label: string | null;
	target: ConceptRef;
}

export interface ConceptDetail {
	code: string;
	label: string | null;
	preferred_name: string | null;
	definition: string | null;
	semantic_types: string[];
	synonyms: string[];
	parents: ConceptRef[];
	children: ConceptRef[];
	roles: Relationship[];
	associations: Relationship[];
	incoming_roles: Relationship[];
}

export interface SearchHit {
	code: string;
	label: string | null;
	semantic_type: string | null;
	matched_synonym: string | null;
}

export interface SearchPage {
	query: string;
	total: number;
	limit: number;
	offset: number;
	hits: SearchHit[];
}

export interface GraphNode {
	code: string;
	label: string | null;
	semantic_type: string | null;
}

export type EdgeKind = 'subClassOf' | 'role' | 'association';

export interface GraphEdge {
	source: string;
	target: string;
	relation: string;
	relation_label: string | null;
	kind: EdgeKind;
}

export interface Neighborhood {
	center: string;
	nodes: GraphNode[];
	edges: GraphEdge[];
}
