import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	try {
		const response = await fetch('/api/health');
		if (response.ok) {
			const body = await response.json();
			return { apiStatus: body.status as string };
		}
		return { apiStatus: 'unreachable' };
	} catch {
		return { apiStatus: 'unreachable' };
	}
};
