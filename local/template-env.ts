import { backend } from './factory';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';

// Keep the ain3sh-dev environment's Factory credentials in its setup script.
// Every computer built from it gets, for dev and prod alike,
// ~/factory/.env.<env> (FACTORY_API_KEY=..., linked as ~/.factory/.env.<env>)
// and ~/factory/<env>-env.json ({FACTORY_API_BASE_URL, FACTORY_API_KEY,
// FACTORY_APP_BASE_URL}, the shape live E2E and eval tooling load), all from
// this laptop's ~/factory/.env.<env>. The keys live in one marked region of
// the script; the setup script's own `droid plugin` steps run with the prod key.
//   bun local/template-env.ts <templateId> check   (diff with keys redacted)
//   bun local/template-env.ts <templateId> apply
const [templateId, mode] = process.argv.slice(2);
if (!templateId || !['check', 'apply'].includes(mode ?? '')) throw new Error('usage: template-env.ts <templateId> check|apply');

const ENVS = {
  dev: { host: 'dev.' },
  prod: { host: '' },
} as const;
type Env = keyof typeof ENVS;
const keyOf = (env: Env) => {
  const m = readFileSync(`${homedir()}/factory/.env.${env}`, 'utf8').match(/^FACTORY_API_KEY=["']?([^"'\s]+)/m);
  if (!m) throw new Error(`no FACTORY_API_KEY in ~/factory/.env.${env}`);
  return m[1];
};
const keys: Record<Env, string> = { dev: keyOf('dev'), prod: keyOf('prod') };

const BEGIN = '# factory creds (software-factory local/template-env.ts)';
const END = '# end factory creds';
// The environment caps its setup script at 10000 characters, so the region
// writes each key once and derives the rest; ENVS names each env's host prefix.
const region = [
  BEGIN,
  ...(Object.keys(ENVS) as Env[]).flatMap((env) => [`cat >"$HOME/factory/.env.${env}" <<'EOF'`, `FACTORY_API_KEY=${keys[env]}`, 'EOF']),
  `fk() { sed -n 's/^FACTORY_API_KEY=//p' "$HOME/factory/.env.$1"; }`,
  `fj() { ln -sfn "$HOME/factory/.env.$1" "$HOME/.factory/.env.$1"; printf '{"FACTORY_API_BASE_URL":"https://%sapi.factory.ai","FACTORY_API_KEY":"%s","FACTORY_APP_BASE_URL":"https://%sapp.factory.ai"}\\n' "$2" "$(fk $1)" "$2" >"$HOME/factory/$1-env.json"; }`,
  ...(Object.keys(ENVS) as Env[]).map((env) => `fj ${env} '${ENVS[env].host}'`),
  END,
].join('\n');

const endpoint = `/api/v0/templates/${templateId}`;
const current = String(((await (await backend(endpoint, { method: 'GET' })).json()) as { setupScript?: string }).setupScript ?? '');
const marked = new RegExp(`${BEGIN.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}\\n[\\s\\S]*?\\n${END}`);
// Before the markers existed, the script carried the .env.dev and .env.prod blocks unmarked.
const legacy = /cat > "\$HOME\/factory\/\.env\.dev" <<'EOF'\nFACTORY_API_KEY=\S+\nEOF\nln -sfn "\$HOME\/factory\/\.env\.dev" "\$HOME\/\.factory\/\.env\.dev"(\ncat > "\$HOME\/factory\/\.env\.prod" <<'EOF'\nFACTORY_API_KEY=\S+\nEOF\nln -sfn "\$HOME\/factory\/\.env\.prod" "\$HOME\/\.factory\/\.env\.prod")?/;
const target = marked.test(current) ? marked : legacy;
if (!target.test(current)) throw new Error('could not find the credentials region in the setup script');
let next = current.replace(target, () => region);
next = next.replace(/^prod_key=.*$/m, 'prod_key="$(fk prod)"');
if (!/^prod_key=/m.test(next)) {
  next = next.replace(/^droid plugin marketplace list/m, 'prod_key="$(fk prod)"\ndroid plugin marketplace list');
}
next = next
  .split('\n')
  .map((l) => (/\bdroid plugin\b/.test(l) && !l.includes('FACTORY_API_KEY="$prod_key"') ? l.replace(/(^|\|\| |\| )droid plugin/g, (_m, p) => `${p}FACTORY_API_KEY="$prod_key" droid plugin`) : l))
  .join('\n');

const redact = (s: string) => s.replaceAll(keys.dev, '<dev key>').replaceAll(keys.prod, '<prod key>').replace(/(FACTORY_API_KEY=)(?!"\$prod_key"|<)\S+/g, '$1<old key>');
const a = redact(current).split('\n'), b = redact(next).split('\n');
let shown = 0;
for (let i = 0, j = 0; (i < a.length || j < b.length) && shown < 60; ) {
  if (a[i] === b[j]) { i++; j++; continue; }
  if (b.slice(j).includes(a[i] ?? '\0')) { console.log(`+ ${b[j++]}`); shown++; }
  else { console.log(`- ${a[i++]}`); shown++; }
}
if (next === current) console.log('no change');
console.log(`setup script: ${current.length} -> ${next.length} characters (limit 10000)`);
if (next.length > 10_000) throw new Error('the setup script would exceed the environment limit of 10000 characters');
if (mode === 'apply' && next !== current) {
  const res = await backend(endpoint, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ setupScript: next }) });
  console.log('PATCH', res.status, res.ok ? '' : redact(await res.text()).slice(0, 600));
}
process.exit(0);
