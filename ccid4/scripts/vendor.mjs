// Copy browser libraries and fonts from node_modules into static/ (run once after npm install).
import { copyFileSync, mkdirSync } from 'node:fs';
const copy = (from, to) => { mkdirSync(to.split('/').slice(0, -1).join('/'), { recursive: true }); copyFileSync(from, to); };
copy('node_modules/htmx.org/dist/htmx.min.js', 'static/vendor/htmx.min.js');
copy('node_modules/@alpinejs/csp/dist/cdn.min.js', 'static/vendor/alpine-csp.min.js');
copy('node_modules/wavesurfer.js/dist/wavesurfer.min.js', 'static/vendor/wavesurfer.min.js');
copy('node_modules/cytoscape/dist/cytoscape.min.js', 'static/vendor/cytoscape.min.js');
for (const w of [400, 500, 600, 700]) for (const s of ['thai', 'latin']) {
  const f = `ibm-plex-sans-thai-${s}-${w}-normal.woff2`;
  copy(`node_modules/@fontsource/ibm-plex-sans-thai/files/${f}`, `static/fonts/${f}`);
}
copy('node_modules/@fontsource/ibm-plex-sans-thai/LICENSE', 'static/fonts/OFL-IBM-Plex-Sans-Thai.txt');
console.log('vendored');
