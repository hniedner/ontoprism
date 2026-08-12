import { ApiRequestError, type RemoteFailureState } from '$lib/api';

export type RemoteSearchResult<T> =
	| { readonly state: 'ready'; readonly data: T }
	| { readonly state: 'empty' }
	| {
			readonly state: 'error';
			readonly status: number;
			readonly remoteState: RemoteFailureState;
			readonly message: string;
	  };

export async function loadRemoteSearch<T>(
	query: string,
	operation: () => Promise<T>
): Promise<RemoteSearchResult<T>> {
	if (!query) return { state: 'empty' };
	try {
		return { state: 'ready', data: await operation() };
	} catch (reason) {
		if (reason instanceof ApiRequestError && reason.remoteState) {
			return {
				state: 'error',
				status: reason.status,
				remoteState: reason.remoteState,
				message: reason.message
			};
		}
		throw reason;
	}
}
