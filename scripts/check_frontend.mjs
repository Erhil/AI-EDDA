import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const checks = JSON.parse(readFileSync(resolve(root, 'scripts/frontend-checks.json'), 'utf8'));
const integration = new Set(['notes-clipboard-review.mjs', 'drag-preview-review.cjs', 'partial-split-drag-independent.cjs']);
const selected = process.argv.includes('--ui-only') ? checks.filter(check => !integration.has(check.split('/').at(-1))) : checks;
const failures = [];
for (const check of selected) {
  const result = spawnSync(process.execPath, [resolve(root, check)], { cwd: root, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  if (result.status === 0) console.log(`PASS ${check}`);
  else {
    console.log(`FAIL ${check}`);
    failures.push(`--- ${check} ---\n${result.stdout || ''}\n${result.stderr || ''}\n${result.error || ''}`);
  }
}
const log = resolve(root, 'logs/frontend-check-failures.log');
mkdirSync(dirname(log), { recursive: true });
writeFileSync(log, failures.join('\n'));
console.log(`${selected.length - failures.length}/${selected.length} passed; failure details: ${log}`);
if (failures.length) process.exitCode = 1;
