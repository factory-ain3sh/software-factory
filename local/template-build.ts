import { backend } from './factory';

// Rebuild a Factory Environment (execution template) so its image picks up
// this repository's setup script, then report the template's build state.
//   bun local/template-build.ts <templateId> status
//   bun local/template-build.ts <templateId> build
const [templateId, mode] = process.argv.slice(2);
if (!templateId || !['status', 'build'].includes(mode ?? '')) throw new Error('usage: template-build.ts <templateId> status|build');
const endpoint = `/api/v0/templates/${templateId}`;

async function call(path: string, init: RequestInit): Promise<Record<string, unknown>> {
  const res = await backend(path, init);
  const body = await res.text();
  if (!res.ok) throw new Error(`${init.method} ${path}: HTTP ${res.status} ${body.slice(0, 500)}`);
  return JSON.parse(body);
}

const template = mode === 'build' ? await call(`${endpoint}/builds`, { method: 'POST' }) : await call(endpoint, { method: 'GET' });
const build = (template.latestBuild ?? {}) as Record<string, unknown>;
const repos = (build.repositories ?? []) as { name: string; commitSha: string }[];
console.log(JSON.stringify({
  name: template.name,
  status: template.status,
  build: { id: build.id, status: build.status, createdAt: build.createdAt, finishedAt: build.finishedAt, promotedAt: build.promotedAt },
  commits: Object.fromEntries(repos.map((r) => [r.name, r.commitSha?.slice(0, 12)])),
}));
if (build.status === 'failed') console.log(String(build.excerpt ?? '').split('\n').slice(-40).join('\n'));
process.exit(0);
