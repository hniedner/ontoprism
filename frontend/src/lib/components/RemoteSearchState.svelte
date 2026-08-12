<script lang="ts">
	import type { RemoteFailureState } from '$lib/api';

	let {
		service,
		state,
		message
	}: { service: string; state: RemoteFailureState; message: string } = $props();
</script>

<div
	data-remote-state={state}
	role="alert"
	class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger"
>
	<p class="font-semibold">Live {service} search {state}</p>
	<p class="mt-1">{message}</p>
</div>
