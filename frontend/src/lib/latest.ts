export interface LatestHandlers<T> {
	ready: (value: T) => void;
	failed: (reason: unknown) => void;
	settled: () => void;
}

export function handleLatest<T>(
	request: Promise<T>,
	handlers: LatestHandlers<T>,
	cancelRequest: () => void = () => undefined
): () => void {
	let active = true;
	request
		.then(
			(value) => {
				if (active) handlers.ready(value);
			},
			(reason) => {
				if (active) handlers.failed(reason);
			}
		)
		.finally(() => {
			if (active) handlers.settled();
		});
	return () => {
		active = false;
		cancelRequest();
	};
}
