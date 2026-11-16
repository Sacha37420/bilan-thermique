// Bibliothèques servies à la page depuis l'image (jamais par URL, jamais file://) :
// injectées par page.addScriptTag({content}).

import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

export function chargerBibliotheques() {
  const plotly = fs.readFileSync(require.resolve('plotly.js-dist-min'), 'utf8');
  // three 0.180 n'a plus de build UMD : on enveloppe le build CommonJS, exactement
  // comme le backend pour son HTML exporté.
  const threeCjs = fs.readFileSync(require.resolve('three'), 'utf8');
  const three = `(function(){var exports={};var module={exports:exports};\n${threeCjs}\n;window.THREE=module.exports;})();`;
  const vue3d = fs.readFileSync(new URL('./vue3d-page.js', import.meta.url), 'utf8');
  return { plotly, three, vue3d };
}
