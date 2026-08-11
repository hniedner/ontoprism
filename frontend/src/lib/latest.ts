export interface LatestHandlers<T> {
	ready: (value: T) => void;
	failed: () => void;
	settled: () => void;
}

export function handleLatest<T>(request: Promise<T>, handlers: LatestHandlers<T>): () => void {
	let active = true;
	request
		.then(
			(value) => {
				if (active) handlers.ready(value);
			},
			() => {
				if (active) handlers.failed();
			}
		)
		.finally(() => {
			if (active) handlers.settled();
		});
	return () => {
		active = false;
	};
}
