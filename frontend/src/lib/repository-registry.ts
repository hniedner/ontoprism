import manifest from '../../../repository-manifest.json' with { type: 'json' };
import type { DataTableFilter } from './components/data-table/types';

export type RepositoryKind = 'local-certified-proxy' | 'remote-live-service';
export type LocalRepositoryId = 'ncit' | 'cadsr' | 'uberon' | 'icdo';
type RemoteRepositoryId = 'clinicaltrials' | 'pubmed';
export type RepositoryId = LocalRepositoryId | RemoteRepositoryId;

export interface GridFilter {
	readonly kind: 'text' | 'categorical';
	readonly text_parameter: string;
	readonly values: Record<string, string>;
	readonly multiple?: boolean;
	readonly source_domain?: string;
}
export interface GridCapabilities {
	readonly sorts: Record<'list' | 'search', string[]>;
	readonly filters: Record<string, GridFilter>;
	readonly pagination: 'offset' | 'cursor';
	readonly query_before_results: boolean;
	readonly metadata: 'certified' | 'remote';
	readonly graph: 'ontology' | 'source-anchors' | 'none';
	readonly links: 'mapping' | 'source-anchors' | 'references' | 'none';
}

function object(value: unknown, allowed?: readonly string[], optional: string[] = []): Record<string, unknown> {
	if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Expected capability object');
	if (allowed && (allowed.some((key) => !Object.hasOwn(value, key)) || Object.keys(value).some((key) => !allowed.includes(key) && !optional.includes(key)))) throw new TypeError('Unknown or missing capability field');
	return value as Record<string, unknown>;
}
function validateSorts(value: unknown): void {
	const sorts = object(value, ['list', 'search']);
	for (const values of Object.values(sorts)) if (!Array.isArray(values) || !values.length || values.some((v) => typeof v !== 'string' || !v) || new Set(values).size !== values.length) throw new TypeError('Invalid sort domain');
}

function validateFilter(key: string, value: unknown): string {
	const f = object(value, ['kind', 'text_parameter', 'values'], ['multiple', 'source_domain']);
	const values = object(f.values);
	if (!key || !['text', 'categorical'].includes(String(f.kind)) || (f.kind === 'categorical') !== Boolean(Object.keys(values).length || f.source_domain)) throw new TypeError('Invalid filter domain');
	if (f.multiple !== undefined && typeof f.multiple !== 'boolean') throw new TypeError('Invalid multiplicity');
	if (f.source_domain !== undefined && (typeof f.source_domain !== 'string' || !f.source_domain)) throw new TypeError('Invalid source domain');
	if (f.source_domain !== undefined && Object.keys(values).length) throw new TypeError('Filter values and source domain are mutually exclusive');
	if (Object.entries(values).some(([k, v]) => !k || typeof v !== 'string' || !v)) throw new TypeError('Invalid filter value');
	if (typeof f.text_parameter !== 'string' || !/^[a-z_]+$/.test(f.text_parameter)) throw new TypeError('Invalid text parameter');
	return f.text_parameter;
}

function validateFilters(value: unknown): void {
	const parameters = new Set<string>();
	for (const [key, filter] of Object.entries(object(value))) {
		const parameter = validateFilter(key, filter);
		if (parameters.has(parameter)) throw new TypeError('Duplicate text parameter');
		parameters.add(parameter);
	}
}

function capabilities(value: unknown, local: boolean): GridCapabilities {
	const c = object(value, ['sorts', 'filters', 'pagination', 'query_before_results', 'metadata', 'graph', 'links']);
	validateSorts(c.sorts);
	validateFilters(c.filters);
	if (c.metadata !== (local ? 'certified' : 'remote') || typeof c.query_before_results !== 'boolean' || !['offset', 'cursor'].includes(String(c.pagination)) || !['ontology', 'source-anchors', 'none'].includes(String(c.graph)) || !['mapping', 'source-anchors', 'references', 'none'].includes(String(c.links))) throw new TypeError('Contradictory repository capabilities');
	return c as unknown as GridCapabilities;
}

interface RepositoryDescriptorBase {
	readonly label: string;
	readonly path: `/repositories/${string}`;
	readonly capabilities?: GridCapabilities;
	readonly capabilities_by_dataset?: Record<string, GridCapabilities>;
}

interface LocalRepositoryDescriptor extends RepositoryDescriptorBase {
	readonly id: LocalRepositoryId;
	readonly kind: 'local-certified-proxy';
}

interface RemoteRepositoryDescriptor extends RepositoryDescriptorBase {
	readonly id: RemoteRepositoryId;
	readonly kind: 'remote-live-service';
}

type RepositoryDescriptor = LocalRepositoryDescriptor | RemoteRepositoryDescriptor;

const localIds = new Set(['ncit', 'cadsr', 'uberon', 'icdo']);
const remoteIds = new Set(['clinicaltrials', 'pubmed']);
const keys = new Set(['id', 'label', 'path', 'kind', 'capabilities', 'capabilities_by_dataset']);

function descriptorObject(value: unknown): Record<string, unknown> {
	if (typeof value !== 'object' || value === null) throw new TypeError('Repository descriptor must be an object');
	for (const key of Object.keys(value)) {
		if (!keys.has(key)) throw new TypeError(`Repository descriptor field is not allowed: ${key}`);
	}
	return value as Record<string, unknown>;
}

function validIdentity(entry: Record<string, unknown>): boolean {
	return (
		(entry.kind === 'local-certified-proxy' && typeof entry.id === 'string' && localIds.has(entry.id)) ||
		(entry.kind === 'remote-live-service' && typeof entry.id === 'string' && remoteIds.has(entry.id))
	);
}

function validateDeclaredCapabilities(entry: Record<string, unknown>): void {
	if (entry.capabilities !== undefined) capabilities(entry.capabilities, entry.kind === 'local-certified-proxy');
	if (entry.capabilities_by_dataset === undefined) return;
	if (entry.id !== 'icdo' || entry.capabilities !== undefined) throw new TypeError('Invalid dataset capability declaration');
	const datasets = object(entry.capabilities_by_dataset);
	if (!Object.keys(datasets).length) throw new TypeError('Dataset capabilities must not be empty');
	for (const value of Object.values(datasets)) capabilities(value, true);
}

function repositoryDescriptor(value: unknown): RepositoryDescriptor {
	const entry = descriptorObject(value);
	if (typeof entry.label !== 'string' || typeof entry.path !== 'string') throw new TypeError('Repository label and path must be strings');
	if (!validIdentity(entry) || entry.path !== `/repositories/${entry.id}`) throw new TypeError('Repository descriptor kind, id, and path do not agree');
	validateDeclaredCapabilities(entry);
	return entry as unknown as RepositoryDescriptor;
}

function parseRepositoryRegistry(input: unknown): RepositoryDescriptor[] {
	if (!Array.isArray(input)) throw new TypeError('Repository manifest must be an array');
	return input.map(repositoryDescriptor);
}

export const repositories = parseRepositoryRegistry(manifest);

export function gridCapabilities(id: RepositoryId, dataset?: string): GridCapabilities {
	const repository = repositories.find((entry) => entry.id === id);
	const found = dataset ? repository?.capabilities_by_dataset?.[dataset] : repository?.capabilities;
	if (!found) throw new TypeError(`Repository ${id} has no grid declaration`);
	return found;
}

export function gridControls(id: RepositoryId, domains: Record<string, string[]> = {}, dataset?: string) {
	const c = gridCapabilities(id, dataset);
	const sortKeys: Record<string, Partial<Record<'asc' | 'desc', string>>> = {};
	for (const sort of new Set(Object.values(c.sorts).flat())) {
		const [column, direction] = sort.split(':');
		if (direction === 'asc' || direction === 'desc') (sortKeys[column] ??= {})[direction] = sort;
	}
	const categorical = Object.entries(c.filters).filter(([, f]) => f.kind === 'categorical');
	return {
		sortKeys,
		filterKeys: Object.fromEntries(categorical.map(([key]) => [key, key])),
		textKeys: Object.fromEntries(Object.keys(c.filters).map((key) => [key, key])),
		filters: Object.fromEntries(categorical.map(([key, f]) => [key, f.source_domain ? domains[key] ?? [] : Object.keys(f.values)])),
		textFilters: Object.keys(c.filters)
	};
}

export function columnFilter(id: RepositoryId, column: string, ariaLabel: string, domain: string[] = [], dataset?: string): DataTableFilter | undefined {
	const f = gridCapabilities(id, dataset).filters[column];
	if (!f) return undefined;
	return f.kind === 'text' ? { kind: 'text', ariaLabel } : {
		kind: 'categorical', ariaLabel, textFilter: true,
		options: f.source_domain ? domain.map((value) => ({ value, label: value })) : Object.entries(f.values).map(([value, label]) => ({ value, label }))
	};
}
