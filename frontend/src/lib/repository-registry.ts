import manifest from '../../../repository-manifest.json' with { type: 'json' };
import type { DataTableFilter } from './components/data-table/types';

export type RepositoryKind = 'local-certified-proxy' | 'remote-live-service';
export type LocalRepositoryId = 'ncit' | 'cadsr' | 'uberon' | 'icdo';
type RemoteRepositoryId = 'clinicaltrials' | 'pubmed';
export type RepositoryId = LocalRepositoryId | RemoteRepositoryId;

export interface GridFilter {
	readonly kind: 'text' | 'categorical';
	readonly text_parameter?: string;
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
export const repositories = manifest as RepositoryDescriptor[];

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
		textKeys: Object.fromEntries(Object.entries(c.filters).filter(([, f]) => f.text_parameter).map(([key]) => [key, key])),
		filters: Object.fromEntries(categorical.map(([key, f]) => [key, f.source_domain ? domains[key] ?? [] : Object.keys(f.values)])),
		textFilters: Object.entries(c.filters).filter(([, f]) => f.text_parameter).map(([key]) => key)
	};
}

export function columnFilter(id: RepositoryId, column: string, ariaLabel: string, domain: string[] = [], dataset?: string): DataTableFilter | undefined {
	const f = gridCapabilities(id, dataset).filters[column];
	if (!f) return undefined;
	return f.kind === 'text' ? { kind: 'text', ariaLabel } : {
		kind: 'categorical', ariaLabel, textFilter: Boolean(f.text_parameter),
		options: f.source_domain ? domain.map((value) => ({ value, label: value })) : Object.entries(f.values).map(([value, label]) => ({ value, label }))
	};
}
