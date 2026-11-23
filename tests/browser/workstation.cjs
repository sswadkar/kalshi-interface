/* Browser regression checks with synthetic data only. Every request is intercepted;
 * no application server, exchange credentials, or local runtime files are used.
 * See README.md for the optional browser-test setup.
 */
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root=path.resolve(__dirname, '../../static');
const output=process.env.KALSHI_UI_SCREENSHOTS || path.join(require('node:os').tmpdir(), 'kalshi-ui-preview');
const clone=x=>JSON.parse(JSON.stringify(x));
const user={balance:124850, portfolio_value:36200};
const markets=[
 {market_ticker:'SYNTH-ALPHA',team:'North district · Outcome A',yes_bid:54,yes_ask:56,no_bid:44,no_ask:46},
 {market_ticker:'SYNTH-BETA',team:'South district · Outcome B',yes_bid:37,yes_ask:39,no_bid:61,no_ask:63},
 {market_ticker:'SYNTH-GAMMA',team:'East district · Outcome C',yes_bid:68,yes_ask:71,no_bid:29,no_ask:32},
];
const positions=[{ticker:'SYNTH-ALPHA',net_yes_position:25,avg_share_price:52,liquidation_value:13.5,fees_paid_dollars:.32},{ticker:'SYNTH-BETA',net_yes_position:-10,avg_share_price:59,liquidation_value:6.1,fees_paid_dollars:.14}];
const messages=[{type:'INFO',text:'Synthetic preview data. No exchange connection.',timestamp:new Date().toISOString()},{type:'DRY_RUN',text:'BUY 25 YES · SYNTH-ALPHA · simulation complete',timestamp:new Date().toISOString()}];
const group={id:'pair-1',name:'North district · related outcomes',markets:[{ticker:'SYNTH-ALPHA',title:'North district outcome A',yes_ask:46,no_ask:56,is_reverse:false},{ticker:'SYNTH-DELTA',title:'North district outcome A · comparison',yes_ask:58,no_ask:49,is_reverse:false}],opportunity:{market_a:'SYNTH-ALPHA',market_a_side:'yes',market_a_price:46,market_b:'SYNTH-DELTA',market_b_side:'no',market_b_price:49,profit:5,total_cost:95,is_reverse_arb:false}};
let groups=[clone(group)], settings={threshold:5,buffer:1,auto_arb:false,auto_arb_quantity:10,skip_confirmation:false}, dry=true;
let orders=[{order_id:'order-1',ticker:'SYNTH-ALPHA',action:'BUY',side:'YES',price:52,remaining:20,queue_position:125}];
let failStatus=false, failTrade=false, failResting=false, pairErrors=false;
let failConfiguration=false;
const restingBook=[
 {order_id:'rest-1',ticker:'SYNTH-ALPHA',event:'SYNTH-EVENT',title:'Alpha',sub_title:'Outcome <b>A</b>',side:'yes',action:'buy',price:54,best:54,remaining:100,level_size:100,at_top:true,only:true,automated:false,reward_daily:1.25,incentivized:true,queue_ahead:0},
 {order_id:'rest-2',ticker:'SYNTH-BETA',event:'SYNTH-EVENT',title:'Beta',sub_title:'Outcome B',side:'no',action:'buy',price:40,best:44,remaining:30,level_size:500,at_top:false,only:false,automated:true,reward_daily:null,incentivized:false,queue_ahead:null},
];
const scanRows=[{ticker:'SYNTH-CPI',risk:'MACRO',daily_dollars:40,cheap_side:'no',cheap_ref_price:.31,est_daily:3.5,two_sided:true}];
const requests=[], errors=[], external=[];
(async()=>{
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH, headless:true});
 try {
 const context=await browser.newContext({viewport:{width:1440,height:1100}});
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname,method=req.method();
  if(url.hostname!=='workstation.test'){external.push(req.url());return route.abort();}
  if(p.startsWith('/api/')){
   const body=req.postDataJSON();
   if(method!=='GET') requests.push({p,method,body});
   const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
   if(failConfiguration && method==='PUT' && p==='/api/arb/settings') {
    return reply({code:'configuration_save_failed',error:'Could not save configuration. The previous saved configuration is unchanged.'},503);
   }
   if(p==='/api/pages')return reply({pages:[]});
   if(p==='/api/liquidity/orders')return reply({orders:restingBook,count:2,at_top:1,incentivized_count:1,reward_daily_total:1.25});
   if(p==='/api/liquidity/scan')return reply({rows:scanRows});
   if(p.startsWith('/api/orders/reset/'))return reply({success:true,order_id:'rest-1b',ticker:'SYNTH-ALPHA'});
   if((failStatus&&['/api/status','/api/arb/status'].includes(p))||(failResting&&p==='/api/orders/resting'))return reply({error:'Synthetic connection failure'},503);
   if(p==='/api/status') return reply({markets,positions,messages,user_info:user,last_pull:new Date().toISOString(),dry_run:dry});
   if(p==='/api/settings/dry_run'){dry=body.dry_run;return reply({dry_run:dry});}
   if(p==='/api/orders/resting')return reply({resting_orders:orders});
   if(p.startsWith('/api/order/'))return failTrade?reply({error:'Synthetic rejected order'},500):reply({dry_run:dry,fill_count:body.quantity});
   if(p.startsWith('/api/orders/cancel/')){orders=[];return reply({success:true});}
   if(p==='/api/arb/status')return reply({groups,settings,messages,user_info:user,last_pull:new Date().toISOString(),dry_run:dry});
   if(p==='/api/arb/settings'){settings={...settings,...body};return reply({settings});}
   if(p==='/api/arb/execute') return reply({dry_run:dry,errors:pairErrors?['Order 1 partial: 1/10']:[]});
   if(p==='/api/arb/groups'&&method==='POST'){const g={id:'new-group',name:body.name,markets:[],opportunity:null};groups.push(g);return reply({group:g});}
   const match=p.match(/^\/api\/arb\/groups\/([^/]+)(?:\/markets(?:\/(.+))?)?$/);
   if(match){const g=groups.find(g=>g.id===match[1]);if(method==='PUT'){if(body.name!==undefined)g.name=body.name;if(body.payout_verified!==undefined)g.payout_verified=body.payout_verified;return reply({group:g});}if(method==='POST'){g.payout_verified=false;g.markets.push({ticker:body.ticker,title:body.ticker,is_reverse:body.is_reverse,yes_ask:41,no_ask:61});return reply({market:g.markets.at(-1)});}if(method==='DELETE'){if(match[2])g.markets=g.markets.filter(m=>m.ticker!==decodeURIComponent(match[2]));else groups=groups.filter(g=>g.id!==match[1]);return reply({success:true});}}
   throw Error('Unhandled mock API '+method+' '+p);
  }
  const file=p==='/'?'index.html':p==='/arbitrage'?'arbitrage.html':p==='/liquidity'?'liquidity.html':p.startsWith('/static/')?p.slice(8):null;
  if(!file)return route.fulfill({status:404,body:'Not found'});
  const type=file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'text/html';
  return route.fulfill({contentType:type,body:await fs.readFile(path.join(root,file))});
 });
 const page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://workstation.test/');
 await page.getByText('API connected',{exact:true}).waitFor();
 assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
 assert.equal(await page.locator('#totalValue').innerText(),'$1,610.50');
 // Prices arrive in cents and render unscaled.
 assert.match(await page.locator('form[data-ticker="SYNTH-ALPHA"]').locator('xpath=ancestor::tr').innerText(),/54¢\s*\/\s*56¢/);
 assert.match(await page.locator('#positions').innerText(),/52¢/);
 assert.match(await page.locator('#restingOrders').innerText(),/52¢/);
 await page.screenshot({path:output+'/trader-dark.png',fullPage:true});
 const ticket=page.locator('form[data-ticker="SYNTH-ALPHA"]');
 await ticket.locator('input').fill('33');
 await ticket.locator('select').selectOption('no');
 await ticket.locator('input').focus();
 await page.waitForTimeout(2300);
 assert.equal(await ticket.locator('input').inputValue(),'33');
 assert.equal(await ticket.locator('select').inputValue(),'no');
 assert.equal(await page.evaluate(()=>document.activeElement.name),'quantity');
 await ticket.getByRole('button',{name:'Buy',exact:true}).click();
 await page.getByText('Dry run: BUY 33 NO',{exact:false}).waitFor();
 assert.deepEqual(requests.at(-1),{p:'/api/order/buy',method:'POST',body:{ticker:'SYNTH-ALPHA',side:'no',quantity:33}});
 const beforeEnter=requests.length;
 await ticket.locator('input').press('Enter');
 assert.equal(requests.length,beforeEnter);
 await ticket.getByRole('button',{name:'Sell',exact:true}).click();
 await page.getByText('Dry run: SELL 33 NO',{exact:false}).waitFor();
 await page.locator('[data-sell-all="SYNTH-BETA"]').click();
 await page.getByText('Dry run: SELL 10 NO',{exact:false}).waitFor();
 failTrade=true;
 const beforeFailure=requests.length;
 await ticket.getByRole('button',{name:'Buy',exact:true}).click();
 await page.getByText('Synthetic rejected order',{exact:false}).waitFor();
 await page.waitForTimeout(2300);
 assert.equal(requests.length,beforeFailure+1,'Failed mutation must not automatically retry');
 failTrade=false;
 await page.locator('[data-cancel]').click();
 assert.match(await page.locator('#confirmDescription').innerText(),/even when manual trading is in dry run/);
 await page.locator('#confirmAccept').click();
 await page.getByText('Cancellation response received.',{exact:false}).waitFor();
 assert.equal(requests.at(-1).method,'DELETE');
 await page.locator('#dryRun').uncheck();
 await page.locator('#confirmDialog button[value="cancel"]').click();
 assert.equal(dry,true);
 await page.waitForFunction(()=>document.querySelector('#dryRun').checked && !document.querySelector('#dryRun').disabled);
 await page.locator('#dryRun').uncheck();
 await page.locator('#confirmAccept').click();
 await page.getByText('Manual and arbitrage live orders enabled.',{exact:true}).waitFor();
 assert.equal(dry,false);
 await page.locator('#dryRun').check();
 await page.getByText('Manual and arbitrage dry run enabled.',{exact:true}).waitFor();
 failStatus=true;
 await page.getByText('Reconnecting',{exact:true}).waitFor();
 assert.equal(await ticket.getByRole('button',{name:'Buy',exact:true}).isDisabled(),true);
 assert.equal(await ticket.locator('input').inputValue(),'33');
 failStatus=false;
 await page.getByText('API connected',{exact:true}).waitFor();
 assert.equal(await ticket.getByRole('button',{name:'Buy',exact:true}).isEnabled(),true);
 await page.locator('#themeToggle').click();
 assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
 await page.screenshot({path:output+'/trader-light.png',fullPage:true});
 await page.getByRole('link',{name:'Arbitrage',exact:true}).click();
 await page.getByText('API connected',{exact:true}).waitFor();
 assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
 await page.locator('#themeToggle').click();
 await page.screenshot({path:output+'/arbitrage-dark.png',fullPage:true});
 const threshold=page.locator('[name="threshold"]');
 await threshold.fill('7');
 await page.locator('h1').click();
 await page.waitForTimeout(2300);
 assert.equal(await threshold.inputValue(),'7');
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await page.getByText('Strategy settings saved.',{exact:true}).waitFor();
 assert.equal(settings.threshold,7);
 // A known persistence rejection should keep the edit and report the failure,
 // without claiming it was saved or replacing it on the next status poll.
 failConfiguration=true;
 await threshold.fill('8');
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await page.locator('#actionNotice').filter({hasText:'Could not save configuration'}).waitFor();
 assert.equal(settings.threshold,7);
 assert.equal((await page.locator('#actionNotice').innerText()).includes('may have reached'),false);
 await page.waitForTimeout(2300);
 assert.equal(await threshold.inputValue(),'8');
 failConfiguration=false;
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await page.getByText('Strategy settings saved.',{exact:true}).waitFor();
 assert.equal(settings.threshold,8);
 const pair=page.locator('section.group').filter({hasText:'North district · related outcomes'});
 await pair.getByText('Unverified · manual only',{exact:true}).waitFor();
 await pair.getByRole('button',{name:'Verify payouts',exact:true}).click();
 assert.match(await page.locator('#confirmDescription').innerText(),/NOT complementary/);
 await page.locator('#confirmAccept').click();
 await pair.getByText('Payouts verified · auto-eligible',{exact:true}).waitFor();
 assert.equal(groups[0].payout_verified,true);
 assert.deepEqual(requests.at(-1),{p:'/api/arb/groups/pair-1',method:'PUT',body:{payout_verified:true}});
 await page.locator('#opportunities input').fill('17');
 await page.waitForTimeout(2300);
 assert.equal(await page.locator('#opportunities input').inputValue(),'17');
 await page.getByRole('button',{name:'Review pair',exact:true}).click();
 assert.match(await page.locator('#confirmDescription').innerText(),/17 contracts per leg/);
 await page.locator('#confirmAccept').click();
 await page.getByText('Dry run complete. No paired orders submitted.',{exact:true}).waitFor();
 assert.deepEqual(requests.at(-1).body,{ticker_a:'SYNTH-ALPHA',side_a:'yes',ticker_b:'SYNTH-DELTA',side_b:'no',quantity:17});
 pairErrors=true;
 await page.getByRole('button',{name:'Review pair',exact:true}).click();
 await page.locator('#confirmAccept').click();
 await page.getByText('Pair returned errors:',{exact:false}).waitFor();
 pairErrors=false;
 await page.getByRole('button',{name:'+ New group',exact:true}).click();
 await page.locator('#editValue').fill('Synthetic <img src=x onerror=alert(1)> group');
 await page.locator('#editForm button[type="submit"]').click();
 await page.getByRole('heading',{name:/Synthetic <img/}).waitFor();
 assert.equal(await page.locator('#groups img').count(),0,'Untrusted labels must render as text');
 const newGroup=page.locator('section.group').filter({hasText:'Synthetic <img'});
 await newGroup.getByRole('button',{name:'Rename',exact:true}).click();
 await page.locator('#editValue').fill('Second comparison');
 await page.locator('#editForm button[type="submit"]').click();
 await page.getByRole('heading',{name:/Second comparison/}).waitFor();
 const second=page.locator('section.group').filter({hasText:'Second comparison'});
 await second.getByRole('button',{name:'+ Add market',exact:true}).click();
 await page.locator('#editValue').fill('SYNTH-REVERSE');
 await page.locator('#editReverse').check();
 await page.locator('#editForm button[type="submit"]').click();
 await second.getByText('Reverse',{exact:true}).waitFor();
 assert.equal(groups[1].markets[0].is_reverse,true);
 await second.getByRole('button',{name:'Remove SYNTH-REVERSE',exact:true}).click();
 await page.locator('#confirmAccept').click();
 await second.getByText('Add markets to this group',{exact:true}).waitFor();
 await second.getByRole('button',{name:'Delete',exact:true}).click();
 await page.locator('#confirmAccept').click();
 await page.waitForTimeout(2300);
 assert.equal(groups.length,1);
 await page.locator('[name="auto_arb"]').check();
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await page.locator('#confirmAccept').click();
 await page.getByText('Auto execution on',{exact:true}).waitFor();
 assert.equal(settings.auto_arb,true);
 await page.locator('[name="skip_confirmation"]').check();
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await page.getByRole('button',{name:'Execute pair',exact:true}).click();
 await page.getByText('Dry run complete. No paired orders submitted.',{exact:true}).waitFor();
 assert.equal(await page.locator('#confirmDialog').isVisible(),false);
 await page.locator('#themeToggle').click();
 await page.screenshot({path:output+'/arbitrage-light.png',fullPage:true});
 for(const target of ['/','/liquidity','/arbitrage']){
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://workstation.test'+target);
  await page.getByText('API connected',{exact:true}).waitFor();
  const widths=await page.evaluate(()=>({width:innerWidth,content:document.documentElement.scrollWidth}));
  assert.equal(widths.content<=widths.width,true,'Mobile page must not overflow horizontally');
  if(target==='/') {
   const ticketBounds=await page.locator('form[data-ticker="SYNTH-ALPHA"]').boundingBox();
   assert.equal(ticketBounds.x + ticketBounds.width <= widths.width, true, 'Mobile order controls must fit without scrolling');
  }
  await page.screenshot({path:output+({'/':'/trader','/arbitrage':'/arbitrage','/liquidity':'/liquidity'})[target]+'-mobile.png',fullPage:true});
 }
 groups=[];
 await page.waitForTimeout(2300);
 await page.getByText('No market groups yet',{exact:true}).waitFor();
 await page.getByText('No candidates above the threshold',{exact:true}).waitFor();
 // Liquidity monitor: escaped exchange labels, queue flags, requeue confirmation, scanner payload.
 await page.setViewportSize({width:1440,height:1100});
 await page.goto('http://workstation.test/liquidity');
 await page.getByText('API connected',{exact:true}).waitFor();
 assert.equal(await page.locator('#orders b').count(),0,'Exchange labels must render as text');
 await page.locator('#orders').getByText('Only quote',{exact:true}).waitFor();
 assert.equal(await page.locator('#rewardTotal').innerText(),'$1.25');
 assert.equal(await page.locator('[data-requeue="rest-2"]').count(),0,'Automated orders have no manual requeue');
 await page.locator('[data-requeue="rest-1"]').click();
 assert.match(await page.locator('#confirmDescription').innerText(),/back of its price level/);
 await page.locator('#confirmAccept').click();
 await page.getByText('Requeued SYNTH-ALPHA.',{exact:false}).waitFor();
 assert.deepEqual(requests.at(-1),{p:'/api/orders/reset/rest-1',method:'POST',body:null});
 await page.locator('#scanForm [name="series"]').fill('KXCPI');
 await page.locator('#scanForm button').click();
 await page.locator('#scanResults').getByText('Macro release',{exact:true}).waitFor();
 assert.match(await page.locator('#scanResults').innerText(),/31¢/);
 await page.screenshot({path:output+'/liquidity-dark.png',fullPage:true});
 assert.deepEqual(errors,[],'No browser exceptions');
 assert.deepEqual(external,[],'No external requests');
 console.log(JSON.stringify({result:'PASS',checks:['trading payloads','sell all','no implicit Enter trade','no mutation retry','live cancellation confirmation','mode confirmation','polling input and focus preservation','disconnect and recovery','theme persistence','settings preservation','pair payload and partial error','group create/rename/add/remove/delete','payout verification','prices in cents','escaped labels','automation confirmation','skip confirmation','mobile overflow','empty states','arbitrage failed save and retry','liquidity monitor and scanner'],mutations:requests.length,screenshots:output},null,2));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
