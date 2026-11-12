import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const ts = createRequire(import.meta.url)('../../node_modules/typescript');
const cache = new Map();
export function load(path) {
 const file = resolve(dirname(fileURLToPath(import.meta.url)), path);
 function compile(name) {
  if (cache.has(name)) return cache.get(name);
  let source = ts.transpileModule(readFileSync(name, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  source = source.replace(/(from\s+|import\s*)(['"])([^'"]+)\2/g, (all, prefix, quote, spec) => {
   let target;
   if (spec.startsWith('.')) { target = resolve(dirname(name), spec); if (!existsSync(target)) target += '.ts'; target = compile(target); }
   else target = import.meta.resolve(spec);
   return `${prefix}${quote}${target}${quote}`;
  }).replaceAll("'/api/", "'http://localhost/api/");
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  cache.set(name, url); return url;
 }
 return import(compile(file));
}
