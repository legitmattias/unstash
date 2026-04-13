<script lang="ts">
	import 'open-props/style';
	import 'open-props/normalize';

	let { children } = $props();
</script>

<div class="app">
	{@render children()}
</div>

<style>
	.app {
		min-height: 100dvh;
		display: grid;
		place-items: center;
		font-family: var(--font-sans);
		color: var(--text-1);
		background: var(--surface-1);
	}
</style>
