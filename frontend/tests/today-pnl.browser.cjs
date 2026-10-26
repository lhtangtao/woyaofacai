/* Run against an isolated server with real read-only ledger/cache snapshots.
 * PLAYWRIGHT_MODULE may point at the bundled Playwright installation.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.MONEY_TEST_URL || 'http://127.0.0.1:8897';
const out = process.env.MONEY_TEST_OUTPUT || 'reports/web_today_acceptance';
const day = process.env.MONEY_TEST_DAY || '2026-10-23';
const amount = v => v == null ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2});
(async () => {
 const browser = await chromium.launch({channel:'chrome', headless:true});
 const page = await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true});
 const errors = [];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 await page.getByText('今日盈亏',{exact:true}).first().click();
 const dateInput=page.getByLabel('持仓日期');
 await dateInput.waitFor();
 await dateInput.fill(day);
 const getSnapshot = async () => {
  await page.waitForFunction(() => document.querySelector('[data-testid="today-holdings"] tbody tr td')?.textContent !== '正在计算当前筛选…');
  return page.evaluate(async () => {
   const day=document.querySelector('input[aria-label="持仓日期"]').value;
   const channel=document.querySelector('select[aria-label="渠道"]').value;
   const ticker=document.querySelector('input[aria-label="Ticker 筛选"]').value;
   const person=document.querySelector('.workspace-header select')?.value;
   const params=new URLSearchParams({as_of:day,channel,ticker,person_id:person || '1'});
   return (await fetch('/api/v1/holdings/today?'+params)).json();
  });
 };
 async function checkSummary(snapshot) {
  const cards=page.getByTestId('today-cards');
  await cards.getByText(amount(snapshot.summary.pnl),{exact:true}).waitFor();
  await cards.getByText(amount(snapshot.summary.cumulative),{exact:true}).waitFor();
  const rows=page.getByTestId('today-holdings').locator('tbody tr');
  assert.equal(await rows.count(), snapshot.total_rows || 1);
  if (snapshot.rows.length) {
   const indices=[0,1,2,9,3,4,12,11,13,5,6,10];
   const displayed=await rows.evaluateAll(rows=>rows.map(row=>Array.from(row.querySelectorAll('td')).map(c=>c.textContent)));
   const expected=snapshot.rows.map(row=>indices.map(i=>[0,1].includes(i)?String(row[i]??'—'):
    row[i]==null?'—':Number(row[i]).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2,
     ...([5,12,13].includes(i)?{style:'percent'}:{})})));
   assert.deepEqual(displayed,expected,'Rendered financial cells must match snapshot');
  }
 }
 await page.waitForFunction(() => document.querySelectorAll('[data-testid="today-holdings"] tbody tr').length>1);
 let snapshot=await getSnapshot();
 await checkSummary(snapshot);
 assert.equal(await page.locator('.today-cards .today-card').count(),4);
 assert.equal(await page.locator('.today-curve canvas').count(),1);
 assert(snapshot.curve.complete_points>0,'Historical curve needs actual valid points');
 await page.getByText(`${snapshot.curve.complete_points} 个完整五分钟历史点`,{exact:false}).waitFor();
 await page.screenshot({path:path.join(out,'web-historical-all.png'),fullPage:true});
 // Select one broker, verify cards/table, then clear it.
 await page.getByLabel('渠道',{exact:true}).selectOption('长桥');
 snapshot=await getSnapshot(); await checkSummary(snapshot);
 assert(snapshot.rows.every(r=>r[0]==='长桥'));
 await page.screenshot({path:path.join(out,'web-historical-longbridge.png'),fullPage:true});
 await page.locator('.today-pnl').evaluate(el=>{el.scrollTop=el.scrollHeight;});
 await page.screenshot({path:path.join(out,'web-historical-table.png'),fullPage:true});
 await page.locator('.today-pnl').evaluate(el=>{el.scrollTop=0;});
 await page.getByRole('button',{name:'恢复全部渠道',exact:true}).click();
 await page.waitForFunction(() => document.querySelector('select[aria-label="渠道"]').value==='');
 snapshot=await getSnapshot(); await checkSummary(snapshot);
 // Actual canvas click: first channel's bar, historical day has a small positive first bar.
 const canvas=page.locator('.today-channels canvas').first();
 const box=await canvas.boundingBox();
 const count=snapshot.channels.length;
 const barIndex=snapshot.channels.reduce((best,c,i)=>Number(c.pnl)>Number(snapshot.channels[best].pnl)?i:best,0);
 const x=70+(box.width-95)*(barIndex+0.5)/count;
 // This acceptance day's positive bar spans the upper plot; click its body.
 await canvas.click({position:{x,y:100}});
 await page.waitForFunction(() => document.querySelector('select[aria-label="渠道"]').value!=='',null,{timeout:5000});
 const clicked=await page.getByLabel('渠道',{exact:true}).inputValue();
 assert.equal(clicked,snapshot.channels[barIndex].channel);
 await page.getByRole('button',{name:'清除筛选',exact:true}).click();
 await page.getByLabel('Ticker 筛选').fill(snapshot.tickers[0]);
 await page.waitForFunction(() => document.querySelectorAll('[data-testid="today-holdings"] tbody tr').length<20);
 const filtered=await getSnapshot();
 await checkSummary(filtered);
 assert(filtered.rows.every(r=>r[1].includes(snapshot.tickers[0])));
 // Sort presets & export inherit full filter scope.
 await page.getByLabel('排序方式').selectOption('1');
 const downloadPromise=page.waitForEvent('download');
 await page.getByRole('link',{name:'导出 CSV'}).click();
 const download=await downloadPromise;
 await download.saveAs(path.join(out,'filtered-export.csv'));
 // No results must render explicitly instead of an empty chart/blank screen.
 await page.getByLabel('Ticker 筛选').fill('NO_SUCH_TICKER_ACCEPTANCE');
 await page.getByText('当前筛选暂无持仓明细',{exact:true}).waitFor();
 await page.getByText('当前筛选暂无有效曲线数据；未生成模拟点。',{exact:true}).waitFor();
 // Offline/API failure must stay visible and retain the last scope's successful data.
 await page.route('**/api/v1/holdings/today?**',route=>route.abort('internetdisconnected'));
 await page.getByTestId('today-pnl').getByRole('button',{name:'刷新',exact:true}).click();
 await page.locator('[role="alert"]').first().waitFor();
 assert((await page.locator('[role="alert"]').first().innerText()).includes('保留上次成功快照'));
 await page.screenshot({path:path.join(out,'web-offline.png'),fullPage:true});
 await page.unroute('**/api/v1/holdings/today?**');
 await page.getByRole('button',{name:'清除筛选',exact:true}).click();
 await dateInput.fill('2026-10-26');
 await page.waitForFunction(() => document.querySelector('.today-source')?.textContent.includes('所选日期 2026-10-26'));
 await page.screenshot({path:path.join(out,'web-current-all.png'),fullPage:true});
 // Verify the live-view timer actually performs a fresh read after ten seconds.
 let polls=0;
 page.on('request',r=>{if(r.url().includes('/api/v1/holdings/today?')) polls++;});
 await page.waitForTimeout(11000);
 assert(polls>=1,'10-second polling missing');
 await page.setViewportSize({width:900,height:900});
 await page.waitForTimeout(1000);
 const widths=await page.locator('.today-curve').evaluate(el=>({panel:el.clientWidth,canvas:el.querySelector('canvas').clientWidth}));
 assert(widths.canvas<=widths.panel,'Narrow viewport chart must resize');
 await page.screenshot({path:path.join(out,'web-narrow.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 const result={checks:['four cards','real historical curve','channel selection/clear','chart canvas channel click',
  'ticker filter','sort/export','explicit empty state','offline error','current day','10-second polling','900px layout'],
  browserErrors:errors, historicalPoints:snapshot.curve.complete_points, timerRequests:polls};
 fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify(result));
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
