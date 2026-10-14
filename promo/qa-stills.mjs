// QA helper: bundle once, render a list of stills per language into out/qa/<tag>/.
// usage: node qa-stills.mjs <tag> <lang> <frame> [frame...]
import { bundle } from '@remotion/bundler';
import { renderStill, selectComposition } from '@remotion/renderer';
import path from 'node:path';

const [tag, lang, ...frames] = process.argv.slice(2);
const serveUrl = await bundle({ entryPoint: path.resolve('src/index.ts') });
const inputProps = { lang };
const composition = await selectComposition({ serveUrl, id: 'MiruPromo', inputProps });
for (const f of frames) {
  const output = path.resolve(`out/qa/${tag}/${lang}-f${String(f).padStart(4, '0')}.png`);
  await renderStill({ serveUrl, composition, frame: Number(f), output, inputProps, chromiumOptions: { gl: 'angle' } });
  console.log('still', output);
}
