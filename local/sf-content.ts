import {
  collectWorkstreamContent,
  getRemoteWorkstreamContent,
  hydrateWorkstreamContent,
  publishRemoteWorkstreamContent,
  readWorkstreamContentGeneration,
  readWorkstreamContentSnapshot,
  writeWorkstreamContentGeneration,
} from '@factory/software-factory-db';
import { backend } from './factory';
const [mode, WS, dir] = process.argv.slice(2);
if (!dir || !WS) throw new Error('usage: pull|push <workstreamId> <dir>');
const fetch = backend as any;
if (mode === 'pull') {
  const c = await getRemoteWorkstreamContent(fetch, WS);
  await hydrateWorkstreamContent({ workstreamDir: dir, files: c.files });
  await writeWorkstreamContentGeneration(dir, c.generation, c.files, c.snapshotToken);
  console.log(JSON.stringify({ pulled: true, generation: c.generation, files: c.files.length }));
} else if (mode === 'push') {
  const [expectedGeneration, snapshot, files] = await Promise.all([
    readWorkstreamContentGeneration(dir), readWorkstreamContentSnapshot(dir), collectWorkstreamContent(dir),
  ]);
  const result = await publishRemoteWorkstreamContent(fetch, {
    workstreamId: WS, expectedGeneration, computerId: 'local', files,
    baseFiles: snapshot.baseFiles, baseSnapshotToken: snapshot.snapshotToken,
  });
  await writeWorkstreamContentGeneration(dir, result.generation, result.files, result.snapshotToken);
  console.log(JSON.stringify({ pushed: true, generation: result.generation, fileCount: result.fileCount }));
} else throw new Error('bad mode');
process.exit(0);
