const { chromium } = require('playwright');
const { injectAxe, checkA11y } = require('@axe-core/playwright');

async function run(url){
  const browser = await chromium.launch();
  const page = await browser.newPage();
  console.log('[axe] opening', url)
  await page.goto(url, { waitUntil: 'load' });
  await injectAxe(page);
  console.log('[axe] running checks...')
  const results = await checkA11y(page, undefined, { detailedReport: true });
  // checkA11y prints results; also output JSON summary
  console.log('\n[axe] JSON results:')
  try{
    const json = await page.evaluate(async () => await window.axe.run(document));
    console.log(JSON.stringify(json, null, 2));
  }catch(e){
    console.error('[axe] error getting raw results', e)
  }
  await browser.close();
}

const url = process.argv[2] || 'http://localhost:8000/monitor/templates/ui/index.html';
run(url).catch(e=>{ console.error('[axe] failed', e); process.exit(1) })
