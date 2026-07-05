// Types mirroring the backend NCIt read models (ontolib.terminologies.ncit.models).

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

export type EdgeKind = 'subClassOf' | 'role' | 'association' | 'cde-concept';

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
	/** True when the node cap was hit and some neighbors were dropped (partial graph). */
	truncated?: boolean;
}

// Raw SPARQL-JSON (subset) returned by the guarded query endpoint.
export interface SparqlBindingCell {
	value: string;
}

export interface SparqlResultDoc {
	head?: { vars?: string[] };
	results?: { bindings?: Array<Record<string, SparqlBindingCell>> };
	boolean?: boolean;
}

export interface SparqlResponse {
	result: SparqlResultDoc;
	truncated: boolean;
}

// caDSR CDE read models (backend ontolib.repositories.cadsr.models).

export interface ConceptLink {
	concept_code: string;
	concept_name: string;
	concept_type: string | null;
	is_primary: boolean;
}

export interface PermissibleValue {
	value: string;
	meaning: string | null;
	meaning_code: string | null;
}

export interface CdeSummary {
	public_id: string;
	version: string;
	short_name: string;
	long_name: string;
	context: string | null;
	datatype: string | null;
}

export interface CdeDetail extends CdeSummary {
	definition: string | null;
	workflow_status: string | null;
	registration_status: string | null;
	value_domain_type: string | null;
	permissible_values: PermissibleValue[];
	concepts: ConceptLink[];
}

export interface CdeSearchPage {
	query: string;
	total: number;
	limit: number;
	offset: number;
	hits: CdeSummary[];
}

export interface SimilarConcept {
	code: string;
	label: string | null;
	score: number;
}

export interface SimilarCde extends CdeSummary {
	score: number;
}

// Refresh / status.

export interface RepoStatus {
	name: string;
	healthy: boolean;
	version: string | null;
	item_count: number | null;
	error: string | null;
}

export interface RefreshReport {
	refreshed_at: string;
	repositories: RepoStatus[];
}
