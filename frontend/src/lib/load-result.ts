export type HttpErrorStatus = number & { readonly __httpErrorStatus: unique symbol };

export type LoadResult<T> =
	| { readonly state: 'ready'; readonly data: T }
	| { readonly state: 'empty' }
	| { readonly state: 'loading' }
	| { readonly state: 'error'; readonly status: HttpErrorStatus; readonly message: string };

export function ready<T>(data: T): LoadResult<T> {
	return { state: 'ready', data };
}

export function empty<T = never>(): LoadResult<T> {
	return { state: 'empty' };
}

export function failed<T = never>(status: number, message: string): LoadResult<T> {
	if (!Number.isInteger(status) || status < 400 || status > 599) {
		throw new RangeError(`HTTP error status must be an integer from 400 through 599: ${status}`);
	}
	if (!message.trim()) throw new TypeError('HTTP error message must not be blank');
	return { state: 'error', status: status as HttpErrorStatus, message };
}
