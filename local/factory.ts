import { configureCredentialsStorage, getActiveOrganizationId } from '@factory/runtime/auth';
import { fetchAuthenticatedBackend } from '@/api/fetchBackend';
import { getRuntimeAuthConfig } from '@/environment';
import { getEmbeddedKeytar } from '@/utils/keytarEmbedded';

// The CLI reads its endpoints from apps/cli's env files, which bun only loads
// when started from apps/cli. Variables already set in the shell still win.
process.loadEnvFile(new URL('../../factory-mono/apps/cli/.env.production', import.meta.url).pathname);
configureCredentialsStorage({ keytarLoader: getEmbeddedKeytar });
const config = getRuntimeAuthConfig();
if (config.apiBaseUrl !== 'https://api.factory.ai' || (await getActiveOrganizationId(config)) !== '8Gwsui069uJX17GEIpGM') {
  throw new Error('Wrong API or organization');
}

/** The production backend, authenticated as the signed-in user of Ainesh's org. */
export const backend = fetchAuthenticatedBackend;
