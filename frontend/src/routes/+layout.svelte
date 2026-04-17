<script lang="ts">
	import 'open-props/style';
	import 'open-props/normalize';
	import '@fontsource-variable/inter';

	let { children } = $props();
</script>

<div class="app">
	{@render children()}
</div>

<style>
	:global(:root) {
		--font-sans:
			'Inter Variable', system-ui, -apple-system, 'Segoe UI', Roboto, 'Noto Sans', sans-serif;
	}

	.app {
		min-height: 100dvh;
		display: grid;
		place-items: center;
		font-family: var(--font-sans);
		color: var(--text-1);
		background: var(--surface-1);
	}
</style>
