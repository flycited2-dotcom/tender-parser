// Offline tests: no browser, network, credentials or live site interaction.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {listingPages, poiskPages} = require('../tender_parser/browser/rts_sections.cjs');

function fakeBrowser({page=1,last=3,blocked=false,stuck=false,unknown=false,canReset=false}={}) {
  return {
    page,httpError:0,actions:[],
    async evaluate(expression) {
      if(expression.startsWith('JSON.stringify')) return JSON.stringify({
        url:'https://www.rts-tender.ru/poisk/search?id=fake',html:`<div>page ${this.page}</div>`,
        count:10,blocked,login:false,empty:false,signature:`number-${this.page}`,
        pageNumber:unknown?null:this.page,pagerKnown:true,previousEnabled:this.page>1,nextEnabled:this.page<last,
      });
      if(expression.includes('button.mainButtonSearch')) {
        if(!canReset) return false;
        this.actions.push('search');this.page=1;return true;
      }
      const previous=expression.includes('const previous=');
      this.actions.push(previous?'prev':'next');
      if(!stuck) this.page+=previous?-1:1;
      return true;
    },
  };
}
const cfg={source:'rts-poisk',kind:'poisk',host:'www.rts-tender.ru'};
const options={maxPages:10,delayMs:0};

test('native pager returns to page 1 before declaring complete coverage',async()=>{
  const browser=fakeBrowser({page:2}), out={pages:[]};
  assert.equal(await poiskPages(browser,cfg,out,options,Date.now()+5000),true);
  assert.equal(browser.actions[0],'prev');
  assert.ok(out.pages.some(p=>p.html.includes('page 1')));
  assert.equal(browser.page,3);
});
test('native empty-query search form resets server-side pagination in one request',async()=>{
  const browser=fakeBrowser({page:80,last:3,canReset:true}),out={pages:[]};
  assert.equal(await poiskPages(browser,cfg,out,options,Date.now()+5000),true);
  assert.equal(browser.actions[0],'search');
  assert.equal(out.pages.length,3);
});
test('starting forward traversal on page 2 is not a complete collection',async()=>{
  const browser=fakeBrowser({page:2}),out={pages:[]};
  assert.equal(await listingPages(browser,cfg,out,options,Date.now()+5000),false);
  assert.match(out.reason,/first page/);
});
test('CAPTCHA stops before any page click',async()=>{
  const browser=fakeBrowser({blocked:true}),out={pages:[]};
  assert.equal(await poiskPages(browser,cfg,out,options,Date.now()+5000),false);
  assert.deepEqual(browser.actions,[]);
});
test('unknown pager does not invent a first page or navigate arbitrary URLs',async()=>{
  const browser=fakeBrowser({page:2,unknown:true}),out={pages:[]};
  assert.equal(await poiskPages(browser,cfg,out,options,Date.now()+5000),false);
  assert.deepEqual(browser.actions,[]);
});
test('HTTP error does not become a successful empty collection',async()=>{
  const browser=fakeBrowser(),out={pages:[]};browser.httpError=429;
  assert.equal(await listingPages(browser,cfg,out,options,Date.now()+5000),false);
  assert.match(out.reason,/HTTP 429/);
  assert.deepEqual(browser.actions,[]);
});

test('a page jump is partial even when card contents changed',async()=>{
  const browser=fakeBrowser(),out={pages:[]};
  const original=browser.evaluate.bind(browser);
  browser.evaluate=async function(expression){
    if(!expression.startsWith('JSON.stringify')) {this.page+=2;return true;}
    return original(expression);
  };
  assert.equal(await listingPages(browser,cfg,out,options,Date.now()+5000),false);
  assert.match(out.reason,/skipped/);
});

test('native form leaves the site search model untouched',async()=>{
  const vm=require('node:vm');
  let captured;
  const native=()=>({skip:0,top:10,filter:{regions:['Крым']},searchQuery:''});
  const win={getFullServerModel:native};
  const doc={querySelector(selector){
    if(selector==='button.mainButtonSearch') return {click(){captured=win.getFullServerModel('Notifications');}};
    if(selector==='input.search__text') return {value:''};
    return null;
  }};
  const browser=fakeBrowser({page:80,last:1}),out={pages:[]};
  const original=browser.evaluate.bind(browser);
  browser.evaluate=async function(expression){
    if(expression.includes('button.mainButtonSearch')) {
      const result=vm.runInNewContext(expression,{window:win,document:doc});this.page=1;return result;
    }
    return original(expression);
  };
  assert.equal(await poiskPages(browser,cfg,out,options,Date.now()+5000),true);
  assert.equal(captured.skip,0);
  assert.equal(captured.top,10);
  assert.equal(captured.filter.regions[0],'Крым');
  assert.equal(win.getFullServerModel,native);
});

test('visible server error forbids further search submissions and pagination',async()=>{
  const browser=fakeBrowser(),out={pages:[]};
  const original=browser.evaluate.bind(browser);
  browser.evaluate=async function(expression){
    if(expression.startsWith('JSON.stringify')) return JSON.stringify({...JSON.parse(await original(expression)),serverError:true});
    throw new Error('No actions are allowed after a visible listing error');
  };
  assert.equal(await poiskPages(browser,cfg,out,options,Date.now()+5000),false);
  assert.match(out.reason,/server error/);
  assert.equal(out.pages.length,0);
});
