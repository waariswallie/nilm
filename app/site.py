from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from datetime import datetime


site = APIRouter(prefix="/site", tags=["site"])


def _layout(title: str, body_html: str) -> HTMLResponse:
    """Base layout with a simple top nav and dark theme."""
    html = """
<!DOCTYPE html>
<html lang=nl>
<head>
  <meta charset=utf-8>
  <meta name=viewport content="width=device-width, initial-scale=1" />
  <title>__TITLE__ · NILM</title>
  <style>
    :root { --bg:#0f1318; --card:#161c22; --muted:#9aa4ad; --fg:#e6e8ea; --accent:#4ea3ff; }
    body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,Arial,sans-serif}
    a{color:var(--accent);text-decoration:none}
    .nav{display:flex;gap:1rem;align-items:center;background:#101820;padding:.7rem 1rem;position:sticky;top:0}
    .nav a{color:#dbe7f3}
    .nav .sp{flex:1}
    .wrap{padding:1rem}
    .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem;margin-top:1rem}
    .card{background:var(--card);border-radius:10px;padding:1rem;box-shadow:0 1px 2px rgba(0,0,0,.5)}
    .muted{color:var(--muted)}
    input,select,button{background:#1c2430;border:1px solid #2a3644;color:var(--fg);padding:.35rem .5rem;border-radius:6px}
    button{cursor:pointer}
    table{border-collapse:collapse;width:100%;font-size:.85rem}
    th,td{padding:6px;border-bottom:1px solid #24303b;text-align:left}
    th{background:#182229;position:sticky;top:52px;z-index:2}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>function fmt(v,d=2){if(v==null||isNaN(v))return '';return Number(v).toFixed(d);}</script>
  <link rel="icon" href="data:," />
  <meta name="robots" content="noindex" />
  </head>
  <body>
    <div class=nav>
      <b>NILM</b>
      <a href="/site">Home</a>
      <a href="/site/clusters">Clusters</a>
      <a href="/site/devices">Devices</a>
      <a href="/site/sessions">Sessions</a>
      <a href="/site/baseload">Baseload</a>
      <span class=sp></span>
      <a href="/status" title="Status & configuratie (JSON)">Status</a>
      <a href="/overview">Overview</a>
      <a href="/docs">API docs</a>
    </div>
    <div class=wrap>
      __BODY__
      <div class=muted style="margin-top:1rem">Generated __GEN__</div>
    </div>
  </body>
  </html>
    """
    html = html.replace("__TITLE__", title).replace("__BODY__", body_html).replace("__GEN__", datetime.utcnow().isoformat()+"Z")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, max-age=0"})


@site.get("/", response_class=HTMLResponse, summary="Homepage")
def home():
    body = """
    <h1>Welkom</h1>
    <p>Kies een pagina of gebruik de knoppen hieronder om te starten.</p>
    <div class=cards>
      <div class=card>
        <h3>Clusters</h3>
        <p class=muted>Labels, rule-traces en energie per cluster.</p>
        <p><a href="/site/clusters">Open clusters →</a></p>
      </div>
      <div class=card>
        <h3>Devices</h3>
        <p class=muted>Geaggregeerd verbruik per apparaatlabel.</p>
        <p><a href="/site/devices">Open devices →</a></p>
      </div>
      <div class=card>
        <h3>Sessions</h3>
        <p class=muted>Samengevoegde runs (events) per cluster.</p>
        <p><a href="/site/sessions">Open sessions →</a></p>
      </div>
      <div class=card>
        <h3>Baseload</h3>
        <p class=muted>Nachtelijke baseload trend.</p>
        <p><a href="/site/baseload">Open baseload →</a></p>
      </div>
    </div>
    """
    return _layout("Home", body)


@site.get("/clusters", response_class=HTMLResponse, summary="Clusters pagina")
def clusters_page(last_days: int = Query(default=7, ge=1, le=30), feature_set: str = Query(default="extended", pattern="^(basic|extended)$"), debug: bool = False):
    tpl = """
    <h1>Clusters</h1>
    <div id=status class=muted></div>
    <div style=\"display:flex;gap:.5rem;align-items:center;margin:.5rem 0 1rem\">
      <label>Dagen <input id=days type=number min=1 max=30 value=__LAST_DAYS__ style=width:80px></label>
  <label>Feature <select id=fs><option value=basic>basic</option><option value=extended selected>extended</option></select></label>
  <label>Serie <select id=sm><option value=pulses selected>pulses</option><option value=sessions>sessions</option></select></label>
      <label><input type=checkbox id=dbg __DEBUG_CHECKED__> Debug</label>
      <button id=go>Herlaad</button>
      <button id=autolabel title=\"Past suggesties toe\">Auto-label</button>
      <label class=muted><input type=checkbox id=ow checked> overwrite</label>
    </div>
    <canvas id=donut height=160></canvas>
    <div class=card style=\"margin-top:1rem\">
      <table id=tbl></table>
    </div>
    <script>
    const daysEl = document.getElementById('days');
    const fsEl = document.getElementById('fs');
    const dbgEl = document.getElementById('dbg');
    const statusEl = document.getElementById('status');
    fsEl.value = '__FEATURE_SET__';
    document.getElementById('go').onclick = () => {
      const q = `?last_days=${daysEl.value}&feature_set=${fsEl.value}&debug=${dbgEl.checked}`;
      location.search = q;
    };
    document.getElementById('autolabel').onclick = async() => {
      const ow = document.getElementById('ow').checked ? 'true':'false';
      statusEl.textContent = 'Auto-label bezig...';
      try{
        const resp = await fetch(`/autolabel?last_days=${daysEl.value}&feature_set=${fsEl.value}&overwrite=${ow}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
        const j = await resp.json();
        statusEl.textContent = `Auto-label klaar: toegepast ${j.applied?.length||0}, overgeslagen ${j.skipped?.length||0}`;
        setTimeout(()=> statusEl.textContent='', 4000);
        await load();
      }catch(e){ statusEl.textContent = 'Fout bij auto-label: '+e; }
    };
    async function load(){
      statusEl.textContent = 'Laden...';
      let list = [];
      try{
        const r = await fetch(`/clusters?last_days=__LAST_DAYS__&feature_set=__FEATURE_SET__&debug=__DEBUG_BOOL__`);
        if(!r.ok){ throw new Error('/clusters -> '+r.status); }
        const j = await r.json();
        list = j.clusters||[];
      }catch(e){
        statusEl.textContent = 'Fout bij laden: '+e+`. Bekijk \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.`;
        return;
      }
      statusEl.textContent = list.length? '' : 'Geen clusters in dit venster. Probeer meer dagen of check \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.';
      // Donut by total energy
      const ctx = document.getElementById('donut');
      if(window._don) window._don.destroy();
      const labels = list.map(c=>'C'+c.cluster);
      const data = list.map(c=>c.total_energy_kWh||0);
      window._don = new Chart(ctx,{type:'doughnut',data:{labels,datasets:[{data}]}});
      // Table
      const tbl = document.getElementById('tbl');
      const head = `<tr><th>ID</th><th>Label</th><th>Bron</th><th>Conf</th><th>Count</th><th>ΔP avg</th><th>Dur med</th><th>E tot</th><th>E evt</th>` + (`__DEBUG_BOOL__`==='true'? '<th>Trace</th>':'' ) + `</tr>`;
      const rows = list.map(c=>{
         const label = c.label||c.suggested_label||'';
         const src = c.label_source || (c.suggested_label? 'suggested':'');
         const conf = c.label_confidence!=null ? fmt(c.label_confidence,2):'';
         let trace = '';
         if('__DEBUG_BOOL__'==='true' && Array.isArray(c.rule_trace)){
             const hits = c.rule_trace.filter(r=>r.matched).map(r=> r.reason? `${r.rule} · ${r.reason}` : r.rule);
             trace = hits.join('; ');
         }
         const traceTd = ('__DEBUG_BOOL__'==='true') ? `<td style=\"max-width:260px\">${trace}</td>` : '';
         return `<tr><td>${c.cluster}</td><td>${label}</td><td>${src}</td><td>${conf}</td><td>${c.count}</td><td>${fmt(c.avg_dP_kW)}</td><td>${fmt(c.median_duration_min)}</td><td>${fmt(c.total_energy_kWh,3)}</td><td>${fmt(c.avg_event_energy_kWh,3)}</td>` + traceTd + `</tr>`;
      }).join('');
      tbl.innerHTML = head + rows;
    }
    load();
    </script>
    """
    body = tpl.replace("__LAST_DAYS__", str(last_days)).replace("__FEATURE_SET__", feature_set).replace("__DEBUG_CHECKED__", ("checked" if debug else "")).replace("__DEBUG_BOOL__", ("true" if debug else "false"))
    return _layout("Clusters", body)


@site.get("/devices", response_class=HTMLResponse, summary="Devices pagina")
def devices_page(last_days: int = Query(default=7, ge=1, le=30), feature_set: str = Query(default="extended", pattern="^(basic|extended)$")):
    tpl = """
    <h1>Devices</h1>
    <div id=status class=muted></div>
    <div style=\"display:flex;gap:.5rem;align-items:center;margin:.5rem 0 1rem\">
      <label>Dagen <input id=days type=number min=1 max=30 value=__LAST_DAYS__ style=width:80px></label>
      <label>Feature <select id=fs><option value=basic>basic</option><option value=extended selected>extended</option></select></label>
      <button id=go>Herlaad</button>
      <button id=stack>Top 5 stapel</button>
    </div>
    <canvas id=bar height=200></canvas>
    <div class=card style=\"margin-top:1rem\"><table id=tbl></table></div>
    <div class=card style=\"margin-top:1rem\">
      <h3 style=\"margin:.2rem 0 .6rem\">Dagverbruik per apparaat</h3>
      <canvas id=series height=200></canvas>
      <div class=muted id=sumline style=\"margin-top:.5rem\"></div>
    </div>
    <script>
  const fsEl = document.getElementById('fs'); fsEl.value='__FEATURE_SET__';
  const smEl = document.getElementById('sm');
  document.getElementById('go').onclick = ()=>{ location.search = `?last_days=${document.getElementById('days').value}&feature_set=${fsEl.value}&series_mode=${smEl.value}`; };
    const statusEl = document.getElementById('status');
    async function load(){
      statusEl.textContent = 'Laden...';
      let list = [];
      try{
  const r = await fetch(`/devices?last_days=__LAST_DAYS__&feature_set=__FEATURE_SET__&include_series=true&include_baseload=true&series_mode=${smEl.value}`);
        if(!r.ok){ throw new Error('/devices -> '+r.status); }
        const j = await r.json();
        list = j.devices||[];
      }catch(e){ statusEl.textContent = 'Fout bij laden: '+e+`. Bekijk \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.`; return; }
      statusEl.textContent = list.length? '' : 'Geen devices in dit venster. Probeer meer dagen of check \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.';
      const ctx = document.getElementById('bar');
      if(window._bar) window._bar.destroy();
      window._bar = new Chart(ctx,{type:'bar',data:{labels:list.map(d=>d.name), datasets:[{label:'kWh', data:list.map(d=>d.total_energy_kWh)}]}});
      const tbl = document.getElementById('tbl');
      const head = '<tr><th>Naam</th><th>Clusters</th><th>Events</th><th>ΔP avg</th><th>E kWh</th><th>Share %</th></tr>';
      const rows = list.map((d,i)=>`<tr data-i="${i}"><td><a href="#" class="devlink">${d.name}</a></td><td>${(d.clusters||[d.cluster]).join(',')}</td><td>${d.events}</td><td>${fmt(d.avg_dP_kW)}</td><td>${fmt(d.total_energy_kWh,3)}</td><td>${fmt(d.energy_share_pct,1)}</td></tr>`).join('');
      tbl.innerHTML = head + rows;
      // Share sum
      const sumPct = list.reduce((acc,d)=> acc + (Number(d.energy_share_pct)||0), 0);
      document.getElementById('sumline').textContent = `Som van Share % (inclusief baseload indien aanwezig): ${sumPct.toFixed(1)}%`;
      // click to show series
      tbl.querySelectorAll('a.devlink').forEach(a=>{
        a.addEventListener('click', (ev)=>{
          ev.preventDefault();
          const i = Number(a.closest('tr').dataset.i);
          const d = list[i];
          const s = (d.series_daily||[]);
          const labels = s.map(x=> x.day);
          const vals = s.map(x=> x.kWh);
          const sctx = document.getElementById('series');
          if(window._ser) window._ser.destroy();
          window._ser = new Chart(sctx,{type:'line', data:{labels, datasets:[{label:d.name+' kWh/dag', data:vals, tension:.2, borderColor:'#51cf66', backgroundColor:'rgba(81,207,102,.15)', fill:true}]}});
        });
      });

      // Top 5 stacked button
      document.getElementById('stack').onclick = () => {
        const top = [...list].sort((a,b)=> (b.total_energy_kWh||0)-(a.total_energy_kWh||0)).slice(0,5);
        // Build union of all days
        const daysSet = new Set();
        top.forEach(d=> (d.series_daily||[]).forEach(x=> daysSet.add(x.day)) );
        const labels = Array.from(daysSet).sort();
        const palette = ['#4ea3ff','#51cf66','#ffa94d','#845ef7','#15aabf'];
        const datasets = top.map((d,idx)=>{
          const map = {}; (d.series_daily||[]).forEach(x=>{ map[x.day] = x.kWh; });
          const vals = labels.map(day=> map[day] || 0);
          const color = palette[idx % palette.length];
          return {label:d.name, data:vals, borderColor:color, backgroundColor:color, fill:true, tension:.2, stack:'dev'};
        });
        const sctx = document.getElementById('series');
        if(window._ser) window._ser.destroy();
        window._ser = new Chart(sctx,{type:'line', data:{labels, datasets}, options:{scales:{y:{stacked:true}, x:{stacked:true}}}});
      };
    }
    load();
    </script>
    """
    body = tpl.replace("__LAST_DAYS__", str(last_days)).replace("__FEATURE_SET__", feature_set)
    return _layout("Devices", body)


@site.get("/sessions", response_class=HTMLResponse, summary="Sessions pagina")
def sessions_page(last_days: int = Query(default=7, ge=1, le=30)):
    tpl = """
    <h1>Sessions</h1>
    <div id=status class=muted></div>
    <div style=\"display:flex;gap:.5rem;align-items:center;margin:.5rem 0 1rem\">
      <label>Dagen <input id=days type=number min=1 max=30 value=__LAST_DAYS__ style=width:80px></label>
      <button id=go>Herlaad</button>
    </div>
    <div class=card><table id=tbl></table></div>
    <script>
    document.getElementById('go').onclick = ()=>{ location.search='?last_days='+document.getElementById('days').value; };
    const statusEl = document.getElementById('status');
    async function load(){
       statusEl.textContent = 'Laden...';
       let list = [];
       try{
         const r = await fetch(`/sessions?last_days=__LAST_DAYS__`);
         if(!r.ok){ throw new Error('/sessions -> '+r.status); }
         const j = await r.json();
         list = j.sessions||[];
       }catch(e){ statusEl.textContent = 'Fout bij laden: '+e+`. Bekijk \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.`; return; }
       statusEl.textContent = list.length? '' : 'Geen sessions in dit venster. Probeer meer dagen of check \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.';
       list.sort((a,b)=> (b.total_energy_kWh||0)-(a.total_energy_kWh||0));
       const tbl = document.getElementById('tbl');
       const head = '<tr><th>Label</th><th>Cluster</th><th>Events</th><th>Duur min</th><th>E kWh</th><th>%</th></tr>';
       const rows = list.slice(0,200).map(s=>`<tr><td>${s.label||''}</td><td>${s.cluster}</td><td>${s.n_events}</td><td>${fmt(s.duration_min)}</td><td>${fmt(s.total_energy_kWh,3)}</td><td>${fmt(s.energy_share_pct,1)}</td></tr>`).join('');
       tbl.innerHTML = head + rows;
    }
    load();
    </script>
    """
    body = tpl.replace("__LAST_DAYS__", str(last_days))
    return _layout("Sessions", body)


@site.get("/baseload", response_class=HTMLResponse, summary="Baseload pagina")
def baseload_page(last_days: int = Query(default=30, ge=1, le=365)):
    tpl = """
    <h1>Baseload</h1>
    <div id=status class=muted></div>
    <div style=\"display:flex;gap:.5rem;align-items:center;margin:.5rem 0 1rem\">
      <label>Dagen <input id=days type=number min=1 max=365 value=__LAST_DAYS__ style=width:80px></label>
      <button id=go>Herlaad</button>
    </div>
    <canvas id=line height=220></canvas>
    <script>
    document.getElementById('go').onclick = ()=>{ location.search='?last_days='+document.getElementById('days').value; };
    const statusEl = document.getElementById('status');
    async function load(){
      statusEl.textContent = 'Laden...';
      let base = {};
      try{
        const r = await fetch(`/scan?last_days=__LAST_DAYS__`);
        if(!r.ok){ throw new Error('/scan -> '+r.status); }
        const j = await r.json();
        base = j.baseload||{};
      }catch(e){ statusEl.textContent = 'Fout bij laden: '+e+`. Bekijk \u003ca href=\"/status\" style=\"color:#4ea3ff\"\u003estatus\u003c/a\u003e.`; return; }
      statusEl.textContent = Object.keys(base).length? '' : 'Geen baseload data gevonden in dit venster.';
      const labels = Object.keys(base).sort();
      const vals = labels.map(k=> base[k]);
      const ctx = document.getElementById('line');
      if(window._line) window._line.destroy();
      window._line = new Chart(ctx,{type:'line', data:{labels, datasets:[{label:'kW', data:vals, tension:.25, borderColor:'#4ea3ff', backgroundColor:'rgba(78,163,255,.15)', fill:true}]}});
    }
    load();
    </script>
    """
    body = tpl.replace("__LAST_DAYS__", str(last_days))
    return _layout("Baseload", body)
