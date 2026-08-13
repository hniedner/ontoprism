// Types mirroring the backend NCIt read models (ontolib.terminologies.ncit.models).

export type RepresentationStatus = 'legacy-precoordinated';

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
	representation_status: RepresentationStatus | null;
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
	representation_status: RepresentationStatus | null;
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
	representation_status: RepresentationStatus | null;
}

export type EdgeKind =
	| 'subClassOf'
	| 'role'
	| 'association'
	| 'cde-concept'
	| 'part_of'
	| 'other-restriction';

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

export type UberonSource = 'uberon' | 'cl';

export interface UberonConceptRef {
	code: string;
	source: UberonSource;
	label: string | null;
}

export interface UberonRelationship {
	relation: string;
	relation_label: string | null;
	kind: 'subClassOf' | 'part_of' | 'other-restriction';
	target: UberonConceptRef;
}

export interface UberonConceptDetail {
	code: string;
	source: UberonSource;
	label: string | null;
	definition: string | null;
	synonyms: string[];
	xrefs: string[];
	parents: UberonConceptRef[];
	children: UberonConceptRef[];
	relations: UberonRelationship[];
	truncated: boolean;
}

export interface Alignment {
	code: string;
	system: 'ncit' | 'uberon-cl' | 'icdo';
	version: string;
	predicate: string;
	lifecycle: string;
}

export interface UberonAlignments {
	code: string;
	alignments: Alignment[];
}

export interface UberonSearchHit {
	code: string;
	source: UberonSource;
	label: string | null;
	matched_synonym: string | null;
}

export interface UberonSearchPage {
	query: string;
	total: number;
	limit: number;
	offset: number;
	hits: UberonSearchHit[];
}

export interface UberonNeighborhood {
	center: string;
	nodes: Array<{ code: string; source: UberonSource; label: string | null }>;
	edges: Array<{
		source: string;
		target: string;
		relation: string;
		relation_label: string | null;
		kind: 'subClassOf' | 'part_of' | 'other-restriction';
	}>;
	truncated: boolean;
}

export type IcdoEdition = '3.2' | '4.0';
export type IcdoAxis = 'morphology' | 'topography';
export type IcdoLevel = 'morphology' | 'category' | 'leaf';

export interface IcdoRecord {
	code: string;
	level: IcdoLevel;
	parent_code?: string | null;
	base_morphology?: string | null;
	specificity?: string | null;
	behaviour?: string | null;
	preferred: string | null;
	synonyms: string[];
	related: string[];
	notes: string[];
}

export interface IcdoDetail {
	record: IcdoRecord;
	ncit_alignments: Alignment[];
}

export interface IcdoPage {
	edition: IcdoEdition;
	axis: IcdoAxis;
	query: string;
	total: number;
	limit: number;
	offset: number;
	hits: IcdoRecord[];
}

export interface IcdoCongruenceReport {
	report_identity: string;
	icdo_serving_identity: string;
	uberon_serving_identity: string;
	total: number;
	counts: Record<string, number>;
	rows: Array<{
		code: string;
		classification: 'one-supported-candidate' | 'multiple-candidates' | 'no-candidate' | 'broader-narrower-mismatch' | 'intentionally-unresolved' | 'source-data-anomaly';
		reason: string;
		candidates: string[];
		evidence: Array<{ kind: string; candidate: string; value: string }>;
	}>;
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

// External mapping (issue #82).

export interface ExternalMapping {
	object_id: string;
	system: string;
	version: string;
	predicate: string;
	lifecycle: string;
	confidence: number;
	is_identity: boolean;
}

export interface ConceptMappings {
	code: string;
	mappings: ExternalMapping[];
}

// Decomposition (non-pre-coordinated) read models (backend ontolib.decomposition).

export interface DecompositionConstituent {
	axis: string;
	axis_label: string | null;
	filler: string;
	filler_label: string | null;
	axis_source: string;
	most_specific: boolean;
}

export interface ConceptDecomposition {
	code: string;
	is_legacy_precoordinated: boolean;
	decomposed_on: string | null;
	constituents: DecompositionConstituent[];
}

export interface SimilarCde extends CdeSummary {
	score: number;
}

// Manifest-bound repository certification.

export interface CandidateGraphObservation {
	graph_iri: string;
	triples: number;
}

export interface NcitObservation {
	default_triples: number;
	stated_triples: number;
	named_graphs: CandidateGraphObservation[];
	default_version: string | null;
	stated_version: string | null;
	restriction_count: number;
	has_required_restriction: boolean;
	default_has_stated_only_sentinel: boolean;
	stated_has_stated_only_sentinel: boolean;
}

export interface NcitRepositoryReady {
	state: 'ready';
	repository: 'ncit';
	source_identity: string;
	manifest_identity: string;
	release: string;
	activated_at: string;
	observation: NcitObservation;
}

export interface CadsrSourceMetadata {
	url: string;
	downloaded_at: string;
	etag: string | null;
	last_modified: string | null;
	archive_size: number;
	archive_sha256: string;
	member_count: number;
	member_names_sha256: string;
	first_member_timestamp: string;
	last_member_timestamp: string;
}

export interface CadsrRepositoryReady {
	state: 'ready';
	repository: 'cadsr';
	source_identity: string;
	manifest_identity: string;
	item_count: number;
	source: CadsrSourceMetadata;
}

export interface UberonRepositoryReady {
	state: 'ready';
	repository: 'uberon';
	source_identity: string;
	manifest_identity: string;
	source_sha256: string;
	version_iri: string;
	class_counts: { uberon: number; cl: number };
	observation: {
		version_iri: string | null;
		triples: number;
		has_uberon_lung: boolean;
		has_cell_class: boolean;
		has_ncit_xref: boolean;
		serving: {
			rows: number;
			sha256: string;
			uberon_classes: number;
			cl_classes: number;
			uberon_searchable_classes: number;
			cl_searchable_classes: number;
		} | null;
	};
	activated_at: string;
}

export type RepositoryUnhealthyReason =
	| 'manifest-missing'
	| 'manifest-invalid'
	| 'activation-incomplete'
	| 'activation-mismatch'
	| 'release-mismatch'
	| 'observation-mismatch'
	| 'repository-unreachable';

export interface RepositoryUnhealthy {
	state: 'unhealthy';
	repository: 'ncit' | 'cadsr' | 'uberon' | 'icdo';
	reason: RepositoryUnhealthyReason;
	message: string;
}

export interface IcdoRepositoryReady {
	state: 'ready';
	repository: 'icdo';
	edition: IcdoEdition;
	axis: IcdoAxis;
	source_identity: string;
	serving_identity: string;
	activation_identity: string;
	row_count: number;
	activated_at: string;
}

export type RepositoryMetadata =
	| NcitRepositoryReady
	| CadsrRepositoryReady
	| UberonRepositoryReady
	| IcdoRepositoryReady
	| RepositoryUnhealthy;

export interface RefreshReport {
	refreshed_at: string;
	repositories: RepositoryMetadata[];
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
