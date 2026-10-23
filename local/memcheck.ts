import { readFileSync } from 'node:fs';
import { parseMemory, renderMemory, parseMemoryHeader, renderMemoryHeader } from '@mono/packages/software-factory-db/src/memory';
for (const f of process.argv.slice(2)) {
  const c = readFileSync(f, 'utf8');
  const p = parseMemory(c);
  const h = parseMemoryHeader(p.header);
  console.log(f, 'roundtrip', renderMemory(p) === c, 'header', !!h, h ? renderMemoryHeader(h) === p.header : null);
  if (h) console.log(JSON.stringify(h.durableNotes.map((n: any) => ({ id: n.id, retired: n.retired, len: n.content.length }))));
}
