// Listing-only CDP client. No cookies, credentials, signing, challenge solving or APIs.
// Uses the Node runtime already shipped with Playwright; does not launch a browser.
const fs = require('node:fs');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const REGION_CODES = ['9100000000000', '9200000000000', '9000000000000', '9500000000000'];
const REGION_NAMES = { '9100000000000': /крым/i, '9200000000000': /севастопол/i, '9000000000000': /запорож/i, '9500000000000': /херсон/i };
const CONFIGS = [
  {source: 'rts-market-modern', host: 'market.rts-tender.ru', path: '/search/sell',
    url: 'https://market.rts-tender.ru/search/sell?s=1&' + REGION_CODES.map(v => 'regs=' + v).join('&') + '&sts=0&sts=11&smp=0&isHomeReg=false&tst=0&sort=-PublicationDate', kind: 'market'},
  {source: 'rts-223', host: '223.rts-tender.ru', path: '/supplier/auction/Trade/Search.aspx',
    url: 'https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx', kind: 'cabinet'},
  {source: 'rts-44', host: 'app.rts-tender.ru', path: '/supplier/auction/Trade/Search.aspx',
    url: 'https://app.rts-tender.ru/supplier/auction/Trade/Search.aspx', kind: 'cabinet'},
  {source: 'rts-poisk', host: 'www.rts-tender.ru', path: '/poisk',
    url: 'https://www.rts-tender.ru/poisk/search?id=7a2edb26-ab8d-4fee-86b4-56514059add7', kind: 'poisk'},
];

class CDP {
  constructor(ws) {
    this.ws = ws; this.seq = 0; this.pending = new Map(); this.responses = 0; this.httpError = 0;
    ws.onmessage = event => {
      const data = JSON.parse(event.data);
      if (data.method === 'Network.responseReceived' && ['XHR','Fetch','Document'].includes(data.params?.type)) {
        this.responses++;
        if (data.params.response.status >= 400) this.httpError = data.params.response.status;
      }
      const p = this.pending.get(data.id);
      if (!p) return;
      clearTimeout(p.timer); this.pending.delete(data.id);
      data.error ? p.reject(new Error(data.error.message)) : p.resolve(data.result);
    };
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { ws.close(); reject(new Error('CDP connection timeout')); }, 6000);
      ws.onopen = () => { clearTimeout(timer); resolve(); };
      ws.onerror = () => { clearTimeout(timer); reject(new Error('CDP connection failed')); };
    });
    return new CDP(ws);
  }
  call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('CDP command timeout')); }, 45000);
      this.pending.set(id, {resolve, reject, timer});
      this.ws.send(JSON.stringify({id, method, params}));
    });
  }
  async evaluate(expression) {
    const r = await this.call('Runtime.evaluate', {expression, returnByValue: true});
    if (r.exceptionDetails) throw new Error('Listing DOM changed; JavaScript observation failed');
    return r.result?.value;
  }
  close() {
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error('CDP disconnected')); }
    this.pending.clear(); this.ws.close();
  }
}

// Save just public result rows, never hidden form state or scripts.
const OBSERVE = `JSON.stringify((() => {
  const body = document.body?.innerText || '';
  const rows = [...document.querySelectorAll('tr.jqgrow')].filter(n => n.querySelector('[aria-describedby$="_TradeName"]'));
  const cards = [...document.querySelectorAll('article.catalog__strip, div.card-item')];
  const hasRows = rows.length + cards.length > 0;
  const blocked = !hasRows && /captcha|проверяем ваш браузер|anti-ddos|анти.ddos|превышен максимальный лимит|проверка безопасности/i.test(body);
  const login = !hasRows && (/\\/login|login.aspx|auth\\./i.test(location.href) || /вход в личный кабинет|единая система входа/i.test(body));
  const next = document.querySelector('.p-paginator-next, td[id^="next_"][id*="jqgTrade"], .pagination a[rel="next"], .pagination a.next, a.pagination__next');
  const nextEnabled = !!next && !next.disabled && !next.classList.contains('ui-state-disabled') && !next.classList.contains('p-disabled');
  const empty = !hasRows && /найдено\\s*0(?:\\D|$)|ничего не найдено|по вашему запросу.*не найдено|нет записей для просмотра/i.test(body);
  const errorNode=document.querySelector('#error-handler, .error-handler');
  const serverError=!!errorNode && !!errorNode.getClientRects().length && /запрос не выполнен|что-то пошло не так/i.test(errorNode.innerText);
  return {url:location.href, html:'<body>'+[...rows,...cards].map(n=>n.outerHTML).join('')+'</body>',
    count:rows.length+cards.length, blocked, login, empty, nextEnabled, serverError,
    pagerKnown:!!document.querySelector('#pager, .p-paginator, .ui-pg-table'),
    pageNumber:Number(document.querySelector('#pager li.active .current')?.textContent.trim()) || null,
    previousEnabled:!!document.querySelector('#pager a.prev'),
    filterRegion:document.querySelector('select[id$="ddlRegion_ddlList"]')?.value,
    rowRegions:rows.map(n=>{const c=n.querySelector('[aria-describedby$="_Region"]');return c?.title || c?.textContent || '';}),
    signature:[...rows,...cards].map(n=>n.id || n.querySelector('input[name="Card.CardId"]')?.value ||
      n.querySelector('a[href*="/search/sell/"]')?.getAttribute('href') ||
      n.querySelector('.card-item__about a[href]')?.getAttribute('href') || n.innerText).join('|')};
})())`;

async function observe(cdp) { return JSON.parse(await cdp.evaluate(OBSERVE)); }

async function settled(cdp, previous = null) {
  for (let i = 0; i < 5; i++) {
    const view = await observe(cdp);
    if (view.blocked || view.login || view.serverError || view.empty || (view.count && (!previous || view.signature !== previous))) return view;
    await sleep(1500);
  }
  return await observe(cdp);
}

async function listingPages(cdp, cfg, out, options, deadline) {
  const seen = new Set();
  let firstPage = null;
  for (let i = 0; i < options.maxPages; i++) {
    const view = await settled(cdp);
    if (new URL(view.url).hostname!==cfg.host) {out.reason='login/redirect: manual browser action required';return false;}
    if (cdp.httpError) {out.reason='blocked/network: listing returned HTTP '+cdp.httpError;return false;}
    if (view.blocked || view.login) { out.reason = view.blocked ? 'blocked: manual browser check required' : 'login: sign in manually'; return false; }
    if (view.serverError) {out.reason='listing server error; previous rows preserved';return false;}
    if (view.empty) return true;
    if (!view.count) { out.reason = 'unknown listing DOM; not a confirmed empty result'; return false; }
    if (firstPage === null) firstPage = view.pageNumber;
    const identity=cfg.kind==='poisk' && view.pageNumber ? `${view.pageNumber}:${view.signature}` : view.signature;
    if (seen.has(identity)) { out.reason = 'pagination did not advance'; return false; }
    seen.add(identity);
    out.pages.push({url:view.url, html:view.html});
    if (out.pages.length % 5 === 0) process.stderr.write(`${cfg.source}: captured ${out.pages.length} pages\n`);
    if (!view.nextEnabled) {
      // The poisk pager may change. Do not claim the first page is complete without a known pager.
      if (cfg.kind === 'poisk' && !view.pagerKnown) { out.reason = 'visible poisk page captured; pagination coverage not confirmed'; return false; }
      if (cfg.kind === 'poisk' && firstPage !== 1) {out.reason='listing ended but the first page was not confirmed';return false;}
      return true;
    }
    if (Date.now() + options.delayMs > deadline) { out.reason = 'section time budget reached'; return false; }
    await sleep(options.delayMs);
    await cdp.evaluate(`(() => {const n=document.querySelector('.p-paginator-next, td[id^="next_"][id*="jqgTrade"], .pagination a[rel="next"], .pagination a.next, a.pagination__next'); if(n && !n.disabled && !n.classList.contains('ui-state-disabled') && !n.classList.contains('p-disabled')) {n.click();return true;} return false;})()`);
    const changed = await settled(cdp, view.signature);
    if (cfg.kind==='poisk' && view.pageNumber && changed.pageNumber && changed.pageNumber!==view.pageNumber+1) {
      out.reason='pagination skipped or did not advance the expected page';return false;
    }
    if (changed.signature === view.signature && changed.pageNumber === view.pageNumber && !changed.blocked && !changed.login) { out.reason = 'pagination did not advance'; return false; }
  }
  out.reason = 'page budget reached'; return false;
}

async function poiskPages(cdp, cfg, out, options, deadline) {
  // A saved search keeps its current page server-side. A ?page=1 navigation
  // is not proof of a reset. Use only actual native prev/first links.
  let view = await settled(cdp);
  if(view.serverError) {out.reason='listing server error; manual recovery required before another collection';return false;}
  if (!view.blocked && !view.login && !cdp.httpError) {
    // Re-submit the existing native form. Unlike ?page=1 this resets the saved
    // search server-side. Do not add a keyword or change its regional scope.
    const submitted = await cdp.evaluate(`(() => {
      const button=document.querySelector('button.mainButtonSearch');
      const query=document.querySelector('input.search__text');
      if(!button || !query || query.value.trim()) return false;
      // Re-selecting the SAME sort invokes the observed native handler with
      // its original skip=0/top=10 model and the site's own validateRequest.
      // The React main search button preserves the old page in private state.
      const sort=document.querySelector('select#selectmenu');
      if(sort) sort.dispatchEvent(new Event('change',{bubbles:true}));
      else button.click();
      return true;
    })()`);
    if (submitted) {await sleep(options.delayMs);view=await settled(cdp,view.signature);}
  }
  if (view.serverError) {out.reason='listing server error after native search; no automatic retry';return false;}
  const walked = new Set();
  for (let i = 0; i < options.maxPages; i++) {
    if (cdp.httpError || view.blocked || view.login) {out.reason='blocked/network/login while returning to first page';return false;}
    if (view.pageNumber === 1) return await listingPages(cdp,cfg,out,options,deadline);
    if (!view.count || !view.pageNumber || !view.previousEnabled || walked.has(view.pageNumber)) {
      out.reason='first page not confirmed; native pager changed';return false;
    }
    walked.add(view.pageNumber);
    // Save useful rows even when the time/page budget prevents a full pass.
    out.pages.push({url:view.url,html:view.html});
    if (Date.now()+options.delayMs>deadline) {out.reason='time budget while returning to first page';return false;}
    await sleep(options.delayMs);
    const selected = await cdp.evaluate(`(() => {
      const first=[...document.querySelectorAll('#pager a.page-link')].find(n=>n.textContent.trim()==='1');
      const previous=document.querySelector('#pager a.prev');
      const link=first || previous;
      if(!link) return false;link.click();return true;
    })()`);
    if (!selected) {out.reason='native previous-page link unavailable';return false;}
    const next = await settled(cdp,view.signature);
    if (next.pageNumber >= view.pageNumber) {out.reason='previous-page action did not advance';return false;}
    view=next;
  }
  out.reason='page budget while returning to first page';return false;
}

async function collect(cfg, options) {
  const out = {source:cfg.source, url:cfg.url, pages:[], complete:false, reason:''};
  let cdp;
  try {
    const targets = await (await fetch(options.endpoint + '/json/list', {signal:AbortSignal.timeout(5000)})).json();
    let target = targets.find(t => {try {const u=new URL(t.url);return t.type==='page' && u.hostname===cfg.host && u.pathname.startsWith(cfg.path);}catch{return false;}});
    if (!target && options.readOnly) {out.reason='section tab is not open';return out;}
    if (!target) target = await (await fetch(options.endpoint + '/json/new?' + encodeURIComponent(cfg.url), {method:'PUT',signal:AbortSignal.timeout(8000)})).json();
    cdp = await CDP.connect(target.webSocketDebuggerUrl);
    await cdp.call('Network.enable');
    const initial = await settled(cdp);
    if (initial.serverError) {out.reason='listing server error; manual recovery required before another collection';return out;}
    if (initial.blocked || initial.login) {out.reason=initial.blocked?'blocked: manual browser check required':'login: sign in manually';return out;}
    if (options.readOnly) { if(initial.count) out.pages.push({url:initial.url,html:initial.html});out.reason='read-only diagnostic: visible page only';return out; }
    const deadline = Date.now() + (cfg.kind==='poisk' ? 900000 : options.sectionMs);
    if (cfg.kind === 'market') {
      // Exactly one fresh listing load per run; all categories, four regions.
      await cdp.call('Page.navigate', {url:cfg.url}); await sleep(options.delayMs);
      out.complete = await listingPages(cdp,cfg,out,options,deadline);
    } else if (cfg.kind === 'cabinet') {
      let complete = true;
      for (const code of REGION_CODES) {
        process.stderr.write(`${cfg.source}: region ${code}\n`);
        if (Date.now() > deadline) {out.reason='section time budget reached';complete=false;break;}
        const old = await observe(cdp);
        if (old.blocked || old.login) {out.reason='blocked/login: manual action required';complete=false;break;}
        const responsesBefore = cdp.responses;
        const configured = await cdp.evaluate(`(() => {
          const select=document.querySelector('select[id$="ddlRegion_ddlList"]');
          const button=document.querySelector('a[id$="_btnSearch"]');
          const date=document.querySelector('input[id$="txtApplicationEndDate_txtDateFrom"]');
          if(!select || !button || !date || ![...select.options].some(n=>n.value===${JSON.stringify(code)})) return false;
          select.value=${JSON.stringify(code)}; select.dispatchEvent(new Event('change',{bubbles:true}));
          const now=new Date(); date.value=String(now.getDate()).padStart(2,'0')+'.'+String(now.getMonth()+1).padStart(2,'0')+'.'+now.getFullYear();
          date.dispatchEvent(new Event('change',{bubbles:true})); button.click();return true;
        })()`);
        if (!configured) {out.reason='regional/deadline filter controls not recognized';complete=false;break;}
        await sleep(options.delayMs);
        const filtered = await settled(cdp,old.signature);
        if (cdp.httpError) {out.reason='blocked/network: listing returned HTTP '+cdp.httpError;complete=false;break;}
        if (filtered.blocked || filtered.login) {out.reason='blocked/login: manual action required';complete=false;break;}
        const matchingRegion = filtered.filterRegion===code && filtered.rowRegions.length && filtered.rowRegions.every(r=>REGION_NAMES[code].test(r));
        // A nationwide lot can legitimately occur in BOTH neighboring regions.
        if (filtered.count && filtered.signature === old.signature && !matchingRegion && cdp.responses===responsesBefore) {out.reason='regional search did not update listing';complete=false;break;}
        // A single native 100-row listing is gentler than ten 10-row requests.
        const resized = await cdp.evaluate(`(() => {
          const select=document.querySelector('.ui-pg-selbox');
          if(!select || select.value==='100' || ![...select.options].some(n=>n.value==='100')) return false;
          select.value='100';select.dispatchEvent(new Event('change',{bubbles:true}));return true;
        })()`);
        if (resized) await sleep(options.delayMs);
        const ok = await listingPages(cdp,cfg,out,options,deadline);
        if (!ok) {complete=false;break;}
      }
      out.complete=complete;
    } else {
      // Saved search: preserve the owner's filters; never insert a keyword restriction.
      out.complete=await poiskPages(cdp,cfg,out,options,deadline);
    }
  } catch (e) {out.reason = 'browser unavailable: ' + String(e.message).slice(0,200);}
  finally {cdp?.close();}
  return out;
}

async function main() {
  const input=JSON.parse(fs.readFileSync(0,'utf8'));
  const u=new URL(input.endpoint || 'http://127.0.0.1:9222');
  if(u.hostname!=='127.0.0.1' || u.protocol!=='http:') throw new Error('Only local CDP is allowed');
  const options={endpoint:u.origin, readOnly:!!input.read_only, maxPages:Math.min(120,Math.max(1,input.max_pages||120)),delayMs:6000,sectionMs:240000};
  const sections=[];
  for(const cfg of CONFIGS.filter(c => !Array.isArray(input.sections) || input.sections.includes(c.source))) {
    const result = await collect(cfg,options); sections.push(result);
    process.stderr.write(`${cfg.source}: pages=${result.pages.length}, complete=${result.complete}, ${result.reason}\n`);
  }
  process.stdout.write(JSON.stringify({sections}));
}
if(require.main===module) main().catch(()=>{process.stderr.write('RTS browser collector failed');process.exitCode=2;});
module.exports={CONFIGS,REGION_CODES,OBSERVE,listingPages,poiskPages};
