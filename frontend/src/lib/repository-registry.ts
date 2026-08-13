import manifest from '../../../repository-manifest.json';

export type RepositoryKind = 'local-certified-proxy' | 'remote-live-service';
export type LocalRepositoryId = 'ncit' | 'cadsr' | 'uberon' | 'icdo';
type RemoteRepositoryId = 'clinicaltrials' | 'pubmed';
export type RepositoryId = LocalRepositoryId | RemoteRepositoryId;

interface RepositoryDescriptorBase {
	readonly label: string;
	readonly path: `/repositories/${string}`;
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
const keys = new Set(['id', 'label', 'path', 'kind']);

function parseRepositoryRegistry(input: unknown): RepositoryDescriptor[] {
	if (!Array.isArray(input)) throw new TypeError('Repository manifest must be an array');
	return input.map((value) => {
		if (typeof value !== 'object' || value === null) throw new TypeError('Repository descriptor must be an object');
		for (const key of Object.keys(value)) {
			if (!keys.has(key)) throw new TypeError(`Repository descriptor field is not allowed: ${key}`);
		}
		const entry = value as Record<string, unknown>;
		if (typeof entry.label !== 'string' || typeof entry.path !== 'string')
			throw new TypeError('Repository label and path must be strings');
		const valid =
			(entry.kind === 'local-certified-proxy' && typeof entry.id === 'string' && localIds.has(entry.id)) ||
			(entry.kind === 'remote-live-service' && typeof entry.id === 'string' && remoteIds.has(entry.id));
		if (!valid || entry.path !== `/repositories/${entry.id}`)
			throw new TypeError('Repository descriptor kind, id, and path do not agree');
		return entry as unknown as RepositoryDescriptor;
	});
}

export const repositories = parseRepositoryRegistry(manifest);
