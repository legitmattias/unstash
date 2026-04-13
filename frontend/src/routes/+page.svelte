<script>
	let { data } = $props();
</script>

<main>
	<h1>Unstash</h1>
	<p>Adaptive document search and organization.</p>
	{#if data.apiStatus}
		<p class="status">API: {data.apiStatus}</p>
	{/if}
</main>

<style>
	main {
		text-align: center;
		max-inline-size: var(--size-content-2);
		padding: var(--size-fluid-3);
	}

	h1 {
		font-size: var(--font-size-fluid-3);
		font-weight: var(--font-weight-9);
		color: var(--text-1);
	}

	p {
		font-size: var(--font-size-fluid-1);
		color: var(--text-2);
	}

	.status {
		font-size: var(--font-size-0);
		font-family: var(--font-mono);
		color: var(--text-2);
		opacity: 0.6;
	}
</style>
