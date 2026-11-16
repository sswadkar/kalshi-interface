"""Build the offline architecture viewer from the rendered SVG diagrams.

Run with: python3 docs/build_viewer.py
Render the .mmd sources to SVG first; see architecture.md for the renderer command.
Only documentation files are read or written. No application imports or dependencies.
"""

import base64
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
VIEWS = [
    {
        "id": "07-reorganized", "label": "Current organization", "status": "Implemented structure",
        "summary": "Four feature folders use shared exchange, execution, market-data and portfolio modules inside one application.",
        "notes": [
            "The existing 30 routes and JSON data locations are preserved.",
            "Order writes share an entry point; durable execution and central risk admission are still pending.",
            "Offline tests cover the migration. Existing trading and simulation limitations remain.",
        ],
    },
    {
        "id": "01-current", "label": "Before reorganization", "status": "November 11 snapshot",
        "summary": "Four browser pages feed a server that combines routing, trading decisions, background work and persistence.",
        "notes": [
            "Historical snapshot before the November 16 feature-folder migration.",
            "Manual trading, arbitrage and LP can each submit orders through their own paths.",
            "JSON settings and history exist; a durable order and fill ledger does not.",
        ],
    },
    {
        "id": "02-proposed", "label": "Proposed architecture", "status": "Proposed design",
        "summary": "One application with clear module boundaries, shared data and one owner of every order mutation.",
        "notes": [
            "Strategies propose intentions; the execution service admits, records and submits them.",
            "A transactional journal and reconciliation preserve uncertain outcomes across restarts.",
            "Boxes are modules inside one application, not separate microservices. These guarantees are planned.",
        ],
    },
    {
        "id": "03-execution", "label": "Execution sequence", "status": "Proposed design",
        "summary": "Persist identity before sending an order, retain uncertainty after a timeout, and apply each canonical fill once.",
        "notes": [
            "Duplicate requests return the recorded intention instead of creating a new submission.",
            "Database transactions finish before network I/O; unknown outcomes keep reservations.",
            "Stopping quoting and confirming cancellation are reported as separate facts.",
        ],
    },
    {
        "id": "04-order-lifecycle", "label": "Order lifecycle", "status": "Proposed design",
        "summary": "An order can be open, partially filled, canceling or unresolved. Those states have different consequences.",
        "notes": [
            "A network timeout does not establish rejection or cancellation.",
            "A fill can win the cancellation race; canceled orders can still have filled quantity.",
            "Unknown state remains durable until reconciliation or operator review resolves it.",
        ],
    },
    {
        "id": "05-runtime-recovery", "label": "Startup and recovery", "status": "Proposed design",
        "summary": "Start disarmed, reconcile account state, establish valid data, and require explicit arming before live trading.",
        "notes": [
            "One local executor owns the account; the instance guard is not distributed failover.",
            "A fault pauses new exposure while cancellation and reconciliation remain available.",
            "Bounded shutdown preserves unresolved obligations when the exchange cannot confirm cancellation.",
        ],
    },
    {
        "id": "06-arbitrage", "label": "Arbitrage planning", "status": "Proposed design",
        "summary": "Prove the settlement relationship and executable edge before coordinating two orders with bounded exposure.",
        "notes": [
            "Related markets are comparison candidates until a supported payoff relationship is established.",
            "Limits, fees, quantity, depth and buffers determine executable cost.",
            "The plan tracks unmatched fills; it does not promise a cross-market atomic transaction.",
        ],
    },
]


TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalshi workstation architecture</title>
<style>
:root { color-scheme:light; --ink:#18263a; --muted:#617087; --line:#dce3ec; --blue:#315cce; --paper:#f5f7fb; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:var(--paper); font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
button,a,input { -webkit-tap-highlight-color:transparent; }
button,a { touch-action:manipulation; }
button { font:inherit; cursor:pointer; }
a { color:var(--blue); text-underline-offset:3px; }
button:focus-visible,a:focus-visible,input:focus-visible,.viewport:focus-visible { outline:3px solid #7497f6; outline-offset:3px; }
.layout { display:grid; grid-template-columns:252px minmax(0,1fr); min-height:100vh; }
aside { background:#15243a; color:#edf3ff; padding:30px 20px; }
.brand { font-size:11px; font-weight:750; letter-spacing:.15em; text-transform:uppercase; color:#9db8e8; margin:0 0 12px; }
aside h1 { font-size:24px; line-height:1.15; letter-spacing:-.035em; margin:0 0 12px; font-weight:650; }
.aside-note { font-size:13px; color:#acbcd3; margin:0 0 28px; }
.nav { display:grid; gap:7px; }
.nav button { display:flex; gap:12px; text-align:left; color:#c5d2e5; background:transparent; border:1px solid transparent; border-radius:8px; padding:12px 10px; align-items:center; font-size:13px; }
.nav button:hover { background:#233754; }
.nav button[aria-pressed="true"] { color:#fff; background:#29497a; border-color:#4a6eaa; }
.nav .number { color:#95afd8; font-size:11px; font-variant-numeric:tabular-nums; }
.aside-footer { border-top:1px solid #344359; margin-top:30px; padding-top:18px; font-size:12px; color:#acbcd3; }
.aside-footer a { color:#dae7ff; }
main { min-width:0; padding:28px 32px 24px; }
.topline { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
.tag { border-radius:20px; padding:5px 10px; font-size:11px; font-weight:700; background:#e5ebf8; color:#37578e; }
.tag.observed { background:#fff0d9; color:#805b18; }
.date { color:var(--muted); font-size:12px; }
h2 { font-size:30px; line-height:1.15; letter-spacing:-.035em; margin:17px 0 10px; }
.summary { max-width:980px; color:var(--muted); margin:0; }
.notes { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; margin:22px 0; padding:0; list-style:none; }
.notes li { border-left:2px solid #b9c9df; padding-left:12px; font-size:12px; line-height:1.55; color:#43536b; }
.toolbar { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:11px 14px; background:#fff; border:1px solid var(--line); border-radius:10px 10px 0 0; }
.toolbar button,.toolbar a { padding:6px 10px; border:1px solid var(--line); background:#fff; color:#324b72; border-radius:6px; text-decoration:none; font-size:12px; white-space:nowrap; }
.toolbar button:hover,.toolbar a:hover { background:#edf2fc; }
.zoom-label { display:flex; align-items:center; gap:9px; color:var(--muted); font-size:12px; margin:0 6px; }
input[type=range] { width:115px; accent-color:var(--blue); }
output { width:40px; font-variant-numeric:tabular-nums; }
.spacer { flex:1; }
.viewport { height:calc(100vh - 338px); min-height:430px; max-height:1200px; overflow:auto; border:1px solid var(--line); border-top:0; background:#eef2f7; border-radius:0 0 10px 10px; }
.stage { padding:24px; min-width:100%; min-height:100%; display:flex; justify-content:center; align-items:flex-start; }
#diagram { display:block; max-width:none; height:auto; background:white; box-shadow:0 2px 12px #18263a12; flex-shrink:0; }
.hint { margin:10px 0 0; font-size:12px; color:var(--muted); }
.foot { display:flex; flex-wrap:wrap; gap:16px; margin-top:18px; color:var(--muted); font-size:12px; }
@media (max-width:1050px) { .layout { grid-template-columns:210px minmax(0,1fr); } main { padding:22px; } .notes { gap:12px; } }
@media (max-width:760px) {
 .layout { display:block; } aside { padding:20px; } aside h1 { font-size:23px; } .aside-note,.aside-footer { display:none; }
 .nav { margin-top:20px; display:flex; overflow-x:auto; padding-bottom:4px; } .nav button { flex:0 0 auto; font-size:12px; padding:9px 10px; } .nav .number { display:none; }
 main { padding:20px 14px; } h2 { font-size:26px; } .notes { grid-template-columns:1fr; gap:9px; margin:18px 0; }
 .toolbar { gap:6px; padding:10px; } .zoom-label { margin:0; } input[type=range] { width:90px; } .spacer { display:none; }
 .viewport { height:65vh; min-height:380px; } .stage { padding:14px; }
}
@media (prefers-reduced-motion:reduce) { * { scroll-behavior:auto!important; } }
</style>
</head>
<body>
<div class="layout">
<aside>
  <p class="brand">Kalshi workstation</p>
  <h1>Architecture<br>and overhaul</h1>
  <p class="aside-note">Current implementation and the proposed path to reliable execution.</p>
  <nav class="nav" id="views" aria-label="Architecture diagrams"></nav>
  <div class="aside-footer">Editable diagrams and migration details are in the <a href="architecture.md">architecture document</a>.<br><br>This viewer works offline. The proposed design is not implemented.</div>
</aside>
<main>
  <div class="topline"><span class="tag" id="status"></span><span class="date">Updated November 16, 2026</span></div>
  <h2 id="view-title"></h2>
  <p class="summary" id="summary"></p>
  <ul class="notes" id="notes"></ul>
  <div class="toolbar" aria-label="Diagram controls">
    <button id="fit" type="button">Fit width</button>
    <button id="actual" type="button">100%</button>
    <label class="zoom-label">Zoom <input id="zoom" type="range" min="5" max="200" value="100" aria-label="Diagram zoom"><output id="zoom-value" for="zoom" aria-live="polite">100%</output></label>
    <span class="spacer"></span>
    <a id="download" href="#" download>Download SVG</a>
    <a id="source" href="#">Mermaid source</a>
  </div>
  <div class="viewport" id="viewport" tabindex="0" role="region" aria-label="Scrollable architecture diagram"><div class="stage" id="stage"><img id="diagram" alt=""></div></div>
  <p class="hint">Use 100% for readable labels, then scroll to explore. SVG downloads stay sharp at any zoom.</p>
  <div class="foot"><span>Current organization and planned reliability work are labeled separately.</span><a href="architecture.md">File organization, migration plan and acceptance criteria</a></div>
</main>
</div>
<script id="diagram-data" type="application/json">__DATA__</script>
<script>
(() => {
  const diagrams=JSON.parse(document.getElementById('diagram-data').textContent);
  const byId=new Map(diagrams.map(item=>[item.id,item]));
  const nav=document.getElementById('views'), viewport=document.getElementById('viewport'), image=document.getElementById('diagram'), stage=document.getElementById('stage');
  const zoom=document.getElementById('zoom'), zoomValue=document.getElementById('zoom-value');
  let selected=null, mode='fit';
  function scaleTo(scale) {
    scale=Math.min(2,Math.max(.05,scale));
    const padding=innerWidth<=760?28:48;
    image.style.width=Math.round(selected.width*scale)+'px';
    stage.style.width=Math.max(viewport.clientWidth,Math.round(selected.width*scale)+padding)+'px';
    zoom.value=Math.round(scale*100); zoomValue.textContent=Math.round(scale*100)+'%';
  }
  function fit() { mode='fit'; const padding=innerWidth<=760?28:48; scaleTo(Math.min(1,(viewport.clientWidth-padding)/selected.width)); }
  function select(id) {
    selected=byId.get(id)||diagrams[0];
    document.getElementById('view-title').textContent=selected.label;
    document.getElementById('summary').textContent=selected.summary;
    const status=document.getElementById('status'); status.textContent=selected.status; status.classList.toggle('observed',selected.id==='07-reorganized');
    const notes=document.getElementById('notes'); notes.replaceChildren(...selected.notes.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    image.alt=selected.label+'. '+selected.summary; image.src=selected.data;
    const download=document.getElementById('download');download.href=selected.data;download.download=selected.id+'.svg';
    document.getElementById('source').href='diagrams/'+selected.id+'.mmd';
    nav.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.id===selected.id)));
    viewport.scrollTop=0; viewport.scrollLeft=0; fit();
    history.replaceState(null,'','#'+selected.id);
  }
  diagrams.forEach((item,index)=>{
    const button=document.createElement('button');button.type='button';button.dataset.id=item.id;button.setAttribute('aria-pressed','false');
    const number=document.createElement('span');number.className='number';number.textContent=String(index+1).padStart(2,'0');
    const label=document.createElement('span');label.textContent=item.label;button.append(number,label);button.addEventListener('click',()=>select(item.id));nav.append(button);
  });
  document.getElementById('fit').addEventListener('click',fit);
  document.getElementById('actual').addEventListener('click',()=>{mode='manual';scaleTo(1);});
  zoom.addEventListener('input',()=>{mode='manual';scaleTo(Number(zoom.value)/100);});
  addEventListener('resize',()=>{if(mode==='fit')fit();else scaleTo(Number(zoom.value)/100);});
  addEventListener('hashchange',()=>select(location.hash.slice(1)));
  select(location.hash.slice(1));
})();
</script>
</body>
</html>
'''


def main():
    diagrams = []
    for view in VIEWS:
        path = ROOT / 'diagrams' / (view['id'] + '.svg')
        svg = path.read_bytes()
        element = ET.fromstring(svg)
        width = float(element.attrib['viewBox'].split()[2])
        diagrams.append({**view, 'width': width,
                         'data': 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode('ascii')})
    # Escape HTML-sensitive characters even though titles are authored here.
    data = json.dumps(diagrams).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    destination = ROOT / 'architecture.html'
    destination.write_text(TEMPLATE.replace('__DATA__', data), encoding='utf-8')
    print(f'Built {destination.name} with {len(diagrams)} embedded diagrams')


if __name__ == '__main__':
    main()
