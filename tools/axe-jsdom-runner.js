const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const axe = require('axe-core');

async function run(filePath){
  const html = fs.readFileSync(filePath, 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', resources: 'usable' });
  const { window } = dom;
  // inject axe source into the jsdom window
  const script = window.document.createElement('script');
  script.textContent = axe.source;
  window.document.head.appendChild(script);

  // wait briefly for potential resources
  await new Promise(r => setTimeout(r, 200));

  const results = await window.axe.run(window.document);
  console.log(JSON.stringify(results, null, 2));
}

const target = process.argv[2] || path.join(__dirname, '../monitor/templates/ui/index.html');
run(target).catch(e=>{ console.error('axe-jsdom failed', e); process.exit(1) })
