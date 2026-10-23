import { writeFileSync } from 'node:fs';
import { backend } from './factory';
const [id, out] = process.argv.slice(2);
const messages: unknown[] = [];
let cursor: string | undefined;
for (let page = 0; page < 50; page++) {
  const q = new URLSearchParams({ limit: '100' });
  if (cursor) q.set('cursor', cursor);
  const res = await backend(`/api/v0/sessions/${id}/messages?${q}`, { method: 'GET' });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  const body = await res.json() as { messages: unknown[]; pagination: { nextCursor?: string; hasMore: boolean } };
  messages.push(...body.messages);
  if (!body.pagination?.hasMore) break;
  cursor = body.pagination.nextCursor;
}
writeFileSync(out, JSON.stringify({ messages }));
console.log(JSON.stringify({ messages: messages.length }));
process.exit(0);
