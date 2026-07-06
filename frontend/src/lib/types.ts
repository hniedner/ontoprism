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

// ClinicalTrials.gov v2 read models (backend ontolib.repositories.clinicaltrials.models).

export interface CTInterventionDetail {
	type: string | null;
	name: string;
	description: string | null;
}

export interface CTOutcome {
	measure: string;
	description: string | null;
	time_frame: string | null;
}

export interface CTSponsor {
	name: string;
	role: string | null;
}

export interface CTLocation {
	facility: string | null;
	city: string | null;
	state: string | null;
	country: string | null;
	status: string | null;
}

export interface CTReference {
	pmid: string | null;
	citation: string;
	reference_type: string | null;
}

export interface CTStudySummary {
	nct_id: string;
	title: string;
	status: string | null;
	phase: string | null;
	conditions: string[];
	interventions: string[];
	start_date: string | null;
	enrollment: number | null;
	relevance_score: number;
}

export interface CTStudyDetail {
	nct_id: string;
	title: string;
	official_title: string | null;
	status: string | null;
	phase: string | null;
	study_type: string | null;
	primary_purpose: string | null;
	conditions: string[];
	interventions: CTInterventionDetail[];
	primary_outcomes: CTOutcome[];
	secondary_outcomes: CTOutcome[];
	eligibility_criteria: string | null;
	enrollment: number | null;
	start_date: string | null;
	sponsors: CTSponsor[];
	locations: CTLocation[];
	references: CTReference[];
	url: string;
}

export interface CTSearchRequest {
	condition?: string | null;
	intervention?: string | null;
	term?: string | null;
	status?: string | null;
	phase?: string | null;
	limit?: number;
}

export interface CTStudySearchPage {
	condition: string | null;
	intervention: string | null;
	term: string | null;
	total: number;
	studies: CTStudySummary[];
}

// PubMed E-utilities read models (backend ontolib.repositories.pubmed.models).

export interface PubMedAuthor {
	last_name: string | null;
	fore_name: string | null;
	initials: string | null;
}

export interface MeshTerm {
	descriptor: string;
	qualifiers: string[];
	major_topic: boolean;
}

export interface PubMedArticleSummary {
	pmid: string;
	title: string;
	journal: string | null;
	pub_date: string | null;
	authors: string[];
	doi: string | null;
}

export interface PubMedArticleDetail {
	pmid: string;
	title: string;
	abstract: string | null;
	authors: PubMedAuthor[];
	journal: string | null;
	pub_date: string | null;
	doi: string | null;
	pmc_id: string | null;
	mesh_terms: MeshTerm[];
	keywords: string[];
	url: string;
}

export interface PubMedSearchResult {
	query: string;
	total: number;
	articles: PubMedArticleSummary[];
}

export interface RelatedArticlesResult {
	pmid: string;
	link_type: string;
	related_pmids: string[];
}
