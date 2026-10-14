// ============================================================
// sdv_bridge.js - connects your existing map UI to the real SDV backend AND shows, inside index.html,
// the networking + AUTOSAR architecture in parallel with the map.
// Add to index.html (after messaging.js):   <script src="sdv_bridge.js"></script>
// Tick "Real SDV network mode": cars become separate ECU processes over MQTT; a panel opens under the map.
// ============================================================
(function () {
  const BASE = (location.port === '8000') ? '' : 'http://localhost:8000';
  const SDV = { registered: false, lastN: 0, busy: false, net: { rows: [], log: [] }, lastSource: null };
  window.SDV = SDV;
  const $ = id => document.getElementById(id);
  const enabled = () => !!($('sdvChk') && $('sdvChk').checked);
  const post = (u, b) => fetch(BASE + u, { method: 'POST', body: JSON.stringify(b) });
  const CAN = '#1a73e8', V2V = '#e8710a', CLD = '#188038', SVC = '#9334e6';
  const LCOL = { 'In-vehicle bus (CAN)': CAN, 'V2V wireless': V2V, 'V2Cloud uplink': CLD, 'V2Cloud downlink': CLD, 'Adaptive services (SOME/IP-like)': SVC };

  // id, x1,y1,x2,y2, label, predicate(row), labelx, labely, color
  const EDGES = [
    ['plant', 425, 400, 560, 435, 'plant', r => r.sig == 'plant', 470, 408, CAN],
    ['sensor', 620, 400, 520, 300, 'sensor', r => r.sig == 'sensor', 590, 345, CAN],
    ['cmd', 410, 400, 410, 300, 'cmd (E2E)', r => r.sig == 'cmd' && r.layer.startsWith('In-'), 340, 355, CAN],
    ['up', 430, 215, 430, 135, 'status/verdict', r => ['hpc_status', 'alert', 'msglog'].includes(r.sig) && r.layer.startsWith('In-'), 318, 180, CAN],
    ['dn', 475, 135, 475, 215, 'v2v_in / ota', r => ['v2v_in', 'v2v_cmd', 'ota'].includes(r.sig), 482, 180, CAN],
    ['v2v', 550, 90, 770, 90, 'V2V signed BSM + cmds', r => r.layer == 'V2V wireless', 565, 82, V2V],
    ['cup', 350, 75, 210, 75, 'uplink', r => r.layer == 'V2Cloud uplink', 245, 66, CLD],
    ['cdn', 210, 105, 350, 105, 'signed OTA/cmd', r => r.layer == 'V2Cloud downlink', 225, 124, CLD],
    ['svc', 350, 255, 210, 370, 'AI RPC', r => r.layer.startsWith('Adaptive'), 235, 310, SVC]];

  function box(x, y, w, h, t, s1, s2) {
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9" fill="#f1f5fb" stroke="#9aa9bf" stroke-width="1.5"/>` +
      `<text x="${x + w / 2}" y="${y + 24}" text-anchor="middle" font-size="12" font-weight="700" fill="#202124">${t}</text>` +
      (s1 ? `<text x="${x + w / 2}" y="${y + 40}" text-anchor="middle" font-size="9.5" fill="#5f6368">${s1}</text>` : '') +
      (s2 ? `<text x="${x + w / 2}" y="${y + 53}" text-anchor="middle" font-size="9.5" fill="#5f6368">${s2}</text>` : '');
  }

  function buildSection() {
    const st = document.createElement('style');
    st.textContent = `
      #sdvSection{padding:12px 14px;background:#f8f9fb;border-top:2px solid #1a73e8}
      #sdvSection h3{margin:0 0 8px;font-size:15px} #sdvSection h4{margin:0 0 6px;font-size:12px;color:#5f6368;text-transform:uppercase;letter-spacing:.04em}
      .sg{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:12px;margin-bottom:12px}
      @media(max-width:1100px){.sg{grid-template-columns:1fr}}
      .sc{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.2);padding:10px}
      .flow{stroke-dasharray:7 5;animation:sdash 1s linear infinite}@keyframes sdash{to{stroke-dashoffset:-24}}
      .asx{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}@media(max-width:1300px){.asx{grid-template-columns:1fr}}
      .astitle{font-size:11px;font-weight:700;padding:5px 7px;border-radius:6px 6px 0 0;color:#fff}
      .asl{font-size:11px;padding:5px 7px;border:1px solid #d0d7e2;border-top:none;background:#fafbfd}
      .asl.live{background:#eef6ff} .asl b{font-weight:700}
      .ok{color:#188038}.bad{color:#d93025}.warn{color:#e8710a}.pend{color:#5f6368}
      #sdvSection table{width:100%;border-collapse:collapse;font-size:11.5px} #sdvSection th,#sdvSection td{padding:3px 5px;border-bottom:1px solid #e3e8ef;text-align:left}
      #sdvSection th{color:#5f6368} #sdvSection td.n{text-align:right;font-variant-numeric:tabular-nums}
      .cov{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;font-size:12px}.cov div{padding:3px 0;border-bottom:1px dotted #d0d7e2}
      #sdvLog{max-height:230px;overflow:auto}`;
    document.head.appendChild(st);
    const sec = document.createElement('div');
    sec.id = 'sdvSection'; sec.style.display = 'none';
    sec.innerHTML = `
     <h3>Software-Defined Vehicle architecture - live, in parallel with the map</h3>
     <div class="sg">
      <div class="sc"><h4>Network topology (msgs/s on every link)</h4>
       <svg viewBox="0 0 1000 520" width="100%">
        <rect x="300" y="12" width="440" height="496" rx="14" fill="none" stroke="#9aa9bf" stroke-dasharray="5 4"/>
        <text x="315" y="30" font-size="10" fill="#5f6368">VEHICLE - each car = separate processes; in-vehicle bus = simulated CAN</text>
        ${box(30, 40, 180, 80, 'OEM Cloud', 'cloud.py', 'OTA - telemetry - STL')}
        ${box(30, 355, 180, 80, 'AI service (LSTM)', 'Adaptive service', 'MessageAuthenticity')}
        ${box(350, 40, 200, 95, 'Gateway ECU', 'firewall - HMAC - replay check', 'only external interface')}
        ${box(350, 215, 200, 85, 'HPC (AUTOSAR Adaptive)', 'ADAS - detector - shield', 'asks AI: REAL / FAKE')}
        ${box(350, 400, 150, 70, 'Powertrain MCU', 'Classic - E2E - WdgM')}
        ${box(560, 400, 150, 70, 'Sensor MCU', 'Classic - GNSS')}
        ${box(780, 40, 200, 110, 'Other cars', 'gateways verify signature', 'and platoon membership')}
        <g id="sdEdges"></g>
       </svg>
       <div style="font-size:11px;color:#5f6368"><span style="color:${CAN}">&#9632;</span> CAN bus &nbsp;<span style="color:${V2V}">&#9632;</span> V2V wireless &nbsp;<span style="color:${CLD}">&#9632;</span> V2Cloud &nbsp;<span style="color:${SVC}">&#9632;</span> Adaptive service &nbsp;- thickness / animation speed = traffic rate</div>
      </div>
      <div class="sc"><h4>AUTOSAR stacks (live status from the ECUs)</h4>
       <div class="asx">
        <div><div class="astitle" style="background:#1a73e8">AUTOSAR Classic - MCU</div>
         <div class="asl">Application SWC: powertrain control, sensor</div>
         <div class="asl">RTE: sender-receiver signals</div>
         <div class="asl live" id="asE2E"></div><div class="asl live" id="asWdg"></div>
         <div class="asl">MCAL / CanIf: <code>bus.py</code> (MQTT stands in for CAN)</div></div>
        <div><div class="astitle" style="background:#9334e6">AUTOSAR Adaptive - HPC</div>
         <div class="asl">Application: <b>platoon_adas</b> (ASIL-B) ADAS + detector</div>
         <div class="asl live" id="asSvc"></div>
         <div class="asl">Execution Mgmt: manifest requires MessageAuthenticity</div>
         <div class="asl live" id="asUcm"></div>
         <div class="asl">Safety shield: braking-distance override</div></div>
        <div><div class="astitle" style="background:#188038">Gateway ECU</div>
         <div class="asl">V2V TX: signed BSM + commands</div>
         <div class="asl live" id="asFw"></div>
         <div class="asl">Replay counters (V2V) + seq (cloud)</div>
         <div class="asl live" id="asDet"></div>
         <div class="asl">V2Cloud: telemetry up, signed OTA down</div></div>
       </div>
       <h4 style="margin-top:10px">Traffic by layer</h4><table id="sdRows"></table>
      </div>
     </div>
     <div class="sg">
      <div class="sc"><h4>What is covered (ticks itself from live data)</h4><div class="cov" id="sdCov"></div></div>
      <div class="sc"><h4>Packet log (security of each frame)</h4><div id="sdvLog"></div></div>
     </div>`;
    $('mapWrap').insertAdjacentElement('afterend', sec);
    const g = $('sdEdges');
    EDGES.forEach(e => g.insertAdjacentHTML('beforeend',
      `<line id="sl_${e[0]}" x1="${e[1]}" y1="${e[2]}" x2="${e[3]}" y2="${e[4]}" stroke="${e[9]}" stroke-width="1" opacity=".3"/>` +
      `<text id="st_${e[0]}" x="${e[7]}" y="${e[8]}" font-size="10.5" font-weight="700" fill="${e[9]}"></text>`));
  }

  function injectUI() {
    const h4 = [...document.querySelectorAll('#panel h4')].find(e => e.textContent.includes('Simulation settings'));
    const box = document.createElement('div');
    box.style.cssText = 'margin:8px 0;padding:6px 8px;border:1px solid #1a73e8;border-radius:6px;font-size:12px;';
    box.innerHTML = '<label><input type="checkbox" id="sdvChk"> <b>Real SDV network mode</b></label>' +
      '<div style="color:#555;font-size:11px;">Cars = separate ECU processes (MCU / HPC / Gateway) over MQTT, LSTM verdicts, STL monitoring. ' +
      'Architecture panel opens under the map. Needs <code>python run_all.py</code>. Loop route only.</div>' +
      '<div id="sdvState" style="font-size:11px;color:#1a73e8;margin-top:3px;"></div>';
    h4.parentNode.insertBefore(box, h4);
    $('sdvChk').addEventListener('change', () => setActive(enabled()));
    const p = document.createElement('div');
    p.id = 'stlBox';
    p.style.cssText = 'position:absolute;z-index:1000;bottom:10px;left:350px;width:440px;max-width:calc(100% - 700px);' +
      'background:white;padding:8px 10px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,.3);font-size:11px;display:none;';
    $('mapWrap').appendChild(p);
    buildSection();
  }

  function setActive(on) {
    $('sdvSection').style.display = on ? 'block' : 'none';
    $('mapWrap').style.height = on ? '56vh' : '78vh';
    if (typeof map !== 'undefined' && map) setTimeout(() => map.invalidateSize(), 50);
  }

  function renderSTL(stl) {
    const box = $('stlBox');
    if (!stl || !stl.length) { box.style.display = 'none'; return; }
    const col = { sat: '#188038', viol: '#d93025', pending: '#e8710a', vacuous: '#5f6368' };
    const txt = { sat: 'SATISFIED', viol: 'VIOLATED', pending: 'pending', vacuous: 'not triggered' };
    box.style.display = 'block';
    box.innerHTML = '<div style="font-weight:bold;margin-bottom:4px;">STL runtime monitor (robustness &rho;)</div><table style="width:100%;border-collapse:collapse;">' +
      stl.map(s => `<tr title="${s.formula}"><td style="padding:2px 4px;">${s.name}</td><td style="padding:2px 4px;text-align:right;">${s.rho === null ? '&mdash;' : s.rho.toFixed(2)}</td>` +
        `<td style="padding:2px 4px;font-weight:bold;color:${col[s.status]}">${txt[s.status]}</td></tr>`).join('') + '</table>';
  }

  function renderArch(st, net) {
    const cars = st.cars || [], n = cars.length, sum = k => cars.reduce((a, c) => a + (c[k] || 0), 0);
    // topology edges
    EDGES.forEach(e => {
      const rate = net.rows.filter(e[6]).reduce((a, r) => a + r.rate, 0), l = $('sl_' + e[0]);
      l.setAttribute('stroke-width', rate > 0 ? 1.5 + Math.min(5, Math.log2(1 + rate)) : 1);
      l.setAttribute('opacity', rate > 0 ? 1 : .3);
      l.classList.toggle('flow', rate > 0);
      l.style.animationDuration = (2.5 / (1 + rate / 4)).toFixed(2) + 's';
      $('st_' + e[0]).textContent = rate > 0 ? `${e[5]}: ${rate.toFixed(0)}/s` : '';
    });
    const rate = f => net.rows.filter(f).reduce((a, r) => a + r.rate, 0);
    const wdgOk = cars.filter(c => c.wdg_ok).length, svcOk = cars.filter(c => c.svc_ok).length;
    const e2e = sum('e2e_err'), drops = sum('drops'), rej = sum('rejected');
    const ucm = [...new Set(cars.map(c => c.ucm))].join('/') || '-', ver = [...new Set(cars.map(c => c.version))].join(', ') || '-';
    $('asE2E').innerHTML = `COM + E2E (CRC8 + alive counter): <b class="${e2e ? 'bad' : 'ok'}">${e2e} errors</b>`;
    $('asWdg').innerHTML = `WdgM alive supervision: <b class="${wdgOk == n && n ? 'ok' : 'bad'}">${wdgOk}/${n} OK</b>`;
    $('asSvc').innerHTML = `ara::com MessageAuthenticity: <b class="${svcOk == n && n ? 'ok' : 'warn'}">${svcOk}/${n} bound</b>${SDV.lastSource ? ' - last verdict: <b>' + SDV.lastSource + '</b>' : ''}`;
    $('asUcm').innerHTML = `UCM update manager: <b>${ucm}</b>, software v<b>${ver}</b>`;
    $('asFw').innerHTML = `Firewall (HMAC + membership): <b class="${drops ? 'warn' : 'ok'}">${drops} frames dropped</b>`;
    $('asDet').innerHTML = `HPC V2V detector (2nd line): <b class="${rej ? 'warn' : 'ok'}">${rej} rejected</b>`;
    // traffic table
    $('sdRows').innerHTML = '<tr><th>Layer</th><th>Link</th><th>Signal</th><th>/s</th><th>total</th><th>Protection</th></tr>' +
      net.rows.map(r => `<tr><td style="color:${LCOL[r.layer] || '#555'}">${r.layer}</td><td>${r.link}</td><td>${r.sig}</td><td class="n">${r.rate.toFixed(1)}</td><td class="n">${r.count}</td><td style="color:#5f6368">${r.security}</td></tr>`).join('');
    // coverage checklist
    const stl = st.stl || [], viol = stl.filter(s => s.status == 'viol').length, satn = stl.filter(s => s.status == 'sat').length;
    const items = [
      [n > 0 ? 'ok' : 'pend', 'SDV architecture: MCU + HPC + Gateway', `${n} cars x 4 ECU processes = ${4 * n} network nodes`],
      [rate(r => r.layer.startsWith('In-')) > 0 ? 'ok' : 'pend', 'In-vehicle network (CAN-like)', `${rate(r => r.layer.startsWith('In-')).toFixed(0)} msgs/s`],
      [rate(r => r.layer == 'V2V wireless') > 0 ? 'ok' : 'pend', 'V2V communication (signed)', `${rate(r => r.layer == 'V2V wireless').toFixed(0)} msgs/s`],
      [rate(r => r.layer.startsWith('V2Cloud')) > 0 ? 'ok' : 'pend', 'V2Cloud + signed OTA', `${rate(r => r.layer.startsWith('V2Cloud')).toFixed(0)} msgs/s, v${ver}`],
      [n ? (drops ? 'warn' : 'ok') : 'pend', 'Gateway firewall', `${drops} dropped, ${rej} rejected by HPC`],
      [n ? (e2e || wdgOk < n ? 'bad' : 'ok') : 'pend', 'AUTOSAR Classic: E2E + WdgM', `E2E errors ${e2e}, WdgM ${wdgOk}/${n}`],
      [n ? (svcOk == n ? 'ok' : 'warn') : 'pend', 'AUTOSAR Adaptive: ara::com + UCM', `service ${svcOk}/${n}, UCM ${ucm}`],
      [SDV.lastSource ? 'ok' : 'pend', 'AI: LSTM REAL/FAKE verdicts', SDV.lastSource ? 'last by ' + SDV.lastSource : 'send a message to trigger'],
      [stl.length ? (viol ? 'bad' : 'ok') : 'pend', 'STL runtime verification', `${satn}/5 satisfied, ${viol} violated`]];
    const sym = { ok: '&#10003;', warn: '&#9888;', bad: '&#10007;', pend: '&#9675;' };
    $('sdCov').innerHTML = items.map(i => `<div><b class="${i[0]}">${sym[i[0]]}</b> ${i[1]}<br><span style="color:#5f6368;font-size:11px;margin-left:16px">${i[2]}</span></div>`).join('');
    // packet log
    $('sdvLog').innerHTML = '<table><tr><th>time</th><th>layer</th><th>topic</th><th>protection</th><th>content</th></tr>' +
      net.log.slice(0, 40).map(e => `<tr><td>${new Date(e.t * 1000).toLocaleTimeString()}</td><td style="color:${LCOL[e.layer] || '#555'}">${e.layer.split(' ').slice(0, 2).join(' ')}</td><td>${e.topic}</td><td class="${e.protection == 'unsigned' ? 'bad' : 'ok'}">${e.protection}</td><td>${e.summary}</td></tr>`).join('') + '</table>';
  }

  function ingestMessages(st) {
    (st.msgs || []).forEach(m => {
      if (m.n <= SDV.lastN) return;
      SDV.lastN = m.n; SDV.lastSource = m.source;
      const entry = {
        id: m.n, from: m.from, to: m.toId === 0 ? 'Leader' : 'Car ' + m.toId, toId: m.toId, text: m.text,
        isReal: m.isReal, command: m.command, suspicion: 0, confidence: m.confidence,
        reasons: (m.reasons || []).concat(m.source ? ['verdict by: ' + m.source] : []), time: new Date().toLocaleTimeString()
      };
      (messagesByCar[m.toId] = messagesByCar[m.toId] || []).push(entry);
      allMessages.push(entry);
      if (window.showVehiclePopup) window.showVehiclePopup(m.toId, entry);
      refreshCarPanelIfOpen(m.toId);
      const ns = $('networkStatus');
      if (ns) ns.textContent = `${entry.time} - ${m.from} -> ${entry.to}: "${m.text}" [${m.isReal ? 'REAL' : 'FAKE'} ${m.confidence}% via ${m.source}${m.applied ? ', applied' : ', blocked'}]`;
    });
  }

  function applyState(st) {
    const cars = st.cars || [];
    SDV.st = st;
    $('sdvState').textContent = `backend: ${cars.length}/${currentPlatoon.n} ECU sets online - AI ${st.ai_mode} - secure V2V ${st.secure ? 'ON' : 'OFF'}`;
    if (cars.length === 0) return;
    cars.forEach(c => {
      const v = currentPlatoon.vehicles.find(x => x.id === c.id);
      if (!v) return;
      v.s = c.s; v.v = c.v; v.a = c.a; v.x = c.x; v.y = c.y; v.heading = c.heading; v.distanceTraveled = c.dist;
    });
    simTime = st.sim_t;
    if (simTime + 1e-9 >= nextLogTime) { logSnapshot(); nextLogTime = Math.floor(simTime) + 1; }
    renderLiveMarkers();
    ingestMessages(st);
    renderSTL(st.stl);
    renderArch(st, SDV.net);
  }

  async function pollNet() {
    if (!enabled() || !SDV.registered) return;
    try { SDV.net = await (await fetch(BASE + '/api/net')).json(); } catch (e) { }
  }

  async function sdvTick() {
    if (!currentPlatoon || SDV.busy) return;
    SDV.busy = true;
    try {
      if (!SDV.registered) {
        if (!currentPlatoon.closed) {
          alert('Real SDV mode needs a loop route. Tick "Return to start point (loop)" and run again.');
          $('sdvChk').checked = false; setActive(false);
          return;
        }
        const p = currentPlatoon.path;
        await post('/api/route', { x: p.x, y: p.y, n_cars: currentPlatoon.n, speed: currentPlatoon.targetSpeed,
          spacing: parseFloat($('spacing').value) || 20 });
        SDV.registered = true; SDV.lastN = 0; SDV.lastSource = null;
        history = []; simTime = 0; nextLogTime = 0;
        setStatus('Real SDV mode: launching one process set per car (MCU + HPC + Gateway)...');
      }
      applyState(await (await fetch(`${BASE}/api/state?since=${SDV.lastN}`)).json());
    } catch (e) {
      $('sdvState').textContent = 'backend not reachable - run "python run_all.py" (' + e.message + ')';
    } finally { SDV.busy = false; }
  }

  function sdvSend({ from, to, text }) {
    const ns = $('networkStatus');
    if (ns) ns.textContent = 'Message sent: browser -> cloud -> gateway -> HPC (LSTM verdict)...';
    post('/api/message', { from, to, text }).catch(e => { if (ns) ns.textContent = 'send failed: ' + e.message; });
  }

  function install() {
    if (window.__sdvInstalled) return;
    window.__sdvInstalled = true;
    injectUI();
    const origTick = window.liveTick, origSend = window.sendMessage, origReset = window.resetSimulation;
    window.liveTick = function () { return enabled() ? sdvTick() : origTick(); };
    window.sendMessage = function (a) { return (enabled() && SDV.registered) ? sdvSend(a) : origSend(a); };
    window.resetSimulation = function () {
      origReset();
      SDV.registered = false; SDV.lastN = 0;
      $('stlBox').style.display = 'none';
      if (enabled()) post('/api/stop', {}).catch(() => {});
    };
    setInterval(pollNet, 500);
  }
  window.addEventListener('load', install);
})();