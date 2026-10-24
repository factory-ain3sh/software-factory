import { backend } from './factory';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';

// Keep the ain3sh-dev environment's Factory API keys in its setup script:
// ~/factory/.env.dev (dev backend, used by the connector dev override and by
// droids live-testing against dev) and ~/factory/.env.prod (prod backend),
// both copied from this laptop's files of the same names. The setup script's
// own `droid plugin` steps run with the prod key.
//   bun local/template-env.ts <templateId> check   (diff with keys redacted)
//   bun local/template-env.ts <templateId> apply
const [templateId, mode] = process.argv.slice(2);
if (!templateId || !['check', 'apply'].includes(mode ?? '')) throw new Error('usage: template-env.ts <templateId> check|apply');
const keyOf = (env: string) => {
  const m = readFileSync(`${homedir()}/factory/.env.${env}`, 'utf8').match(/^FACTORY_API_KEY=["']?([^"'\s]+)/m);
  if (!m) throw new Error(`no FACTORY_API_KEY in ~/factory/.env.${env}`);
  return m[1];
};
const keys = { dev: keyOf('dev'), prod: keyOf('prod') };
const endpoint = `/api/v0/templates/${templateId}`;
const current = String(((await (await backend(endpoint, { method: 'GET' })).json()) as { setupScript?: string }).setupScript ?? '');
const envBlock = (env: 'dev' | 'prod') => `cat > "$HOME/factory/.env.${env}" <<'EOF'\nFACTORY_API_KEY=${keys[env]}\nEOF\nln -sfn "$HOME/factory/.env.${env}" "$HOME/.factory/.env.${env}"`;
let next = current.replace(/cat > "\$HOME\/factory\/\.env\.dev" <<'EOF'\nFACTORY_API_KEY=\S+\nEOF\nln -sfn "\$HOME\/factory\/\.env\.dev" "\$HOME\/\.factory\/\.env\.dev"(\ncat > "\$HOME\/factory\/\.env\.prod" <<'EOF'\nFACTORY_API_KEY=\S+\nEOF\nln -sfn "\$HOME\/factory\/\.env\.prod" "\$HOME\/\.factory\/\.env\.prod")?/,
  () => `${envBlock('dev')}\n${envBlock('prod')}`);
if (next === current && !current.includes('.env.prod')) throw new Error('could not find the .env.dev block to update');
const pluginLine = /^(?!prod_key=)(.*)\bdroid plugin\b/m;
if (!/^prod_key=/m.test(next)) {
  next = next.replace(/^droid plugin marketplace list/m, `prod_key="$(sed -n 's/^FACTORY_API_KEY=//p' "$HOME/factory/.env.prod")"\ndroid plugin marketplace list`);
}
next = next.split('\n').map((l) => (/\bdroid plugin\b/.test(l) ? l.replace(/(^|\|\| |\| )droid plugin/g, (_m, p) => `${p}FACTORY_API_KEY="$prod_key" droid plugin`).replace(/(FACTORY_API_KEY="\$prod_key" )+/g, 'FACTORY_API_KEY="$prod_key" ') : l)).join('\n');
void pluginLine;
const redact = (s: string) => s.replaceAll(keys.dev, '<dev key>').replaceAll(keys.prod, '<prod key>').replace(/FACTORY_API_KEY=(?!"\$prod_key")\S+/g, 'FACTORY_API_KEY=<old key>');
const a = redact(current).split('\n'), b = redact(next).split('\n');
for (let i = 0; i < Math.max(a.length, b.length); i++) if (a[i] !== b[i]) console.log(`${i + 1}\n- ${a[i] ?? ''}\n+ ${b[i] ?? ''}`);
if (mode === 'apply') {
  const res = await backend(endpoint, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ setupScript: next }) });
  console.log('PATCH', res.status, (await res.text()).replaceAll(keys.dev, '<dev key>').replaceAll(keys.prod, '<prod key>').slice(0, 300));
}
process.exit(0);
