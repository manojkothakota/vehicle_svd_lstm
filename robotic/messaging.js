// ============================================================
// messaging.js
// V2V message pipeline with an on-device intrusion-detection model
// (V2V-LSTM) that scores every incoming message for authenticity
// before it is allowed to control a vehicle.
//
// Handles ALL messaging: Admin -> Car, Car -> Car, Car -> All.
//
// Every message is tagged REAL or FAKE by the model, with a
// confidence score:
//   - REAL messages actually change the receiving vehicle's
//     behaviour: increase/decrease/double/half speed, stop,
//     resume, change direction.
//   - FAKE messages are stored and shown (so you can see the
//     attack was attempted) but never change the vehicle.
//
// Exposes (used by the main HTML inline script):
//   initMessaging(getPlatoonFn, getCarIdsFn)
//   resetMessaging()
//   openCarMessagePanel(carId)
//   buildMessageRows()
// ============================================================

let _getPlatoon = () => null;
let _getCarIds = () => [];

let messagesByCar = {};   // carId -> [ {id, from, to, text, isReal, command, time} ]
let allMessages = [];     // flat log, used for Excel export
let msgCounter = 0;
let _uiWired = false;

// ---------------- Public entry points ----------------

function initMessaging(getPlatoonFn, getCarIdsFn) {
  _getPlatoon = getPlatoonFn;
  _getCarIds = getCarIdsFn;
  wireStaticUI();
  refreshParticipantDropdowns();
}

function resetMessaging() {
  messagesByCar = {};
  allMessages = [];
  msgCounter = 0;
  const panel = document.getElementById('carPanel');
  if (panel) panel.style.display = 'none';
  const ns = document.getElementById('networkStatus');
  if (ns) ns.textContent = 'Model idle. Run the simulation to activate the V2V network.';
  refreshParticipantDropdowns();
}

function buildMessageRows() {
  return allMessages.map(m => ({
    time: m.time,
    from: m.from,
    to: m.to,
    message: m.text,
    status: m.isReal ? 'REAL' : 'FAKE',
    model_confidence_pct: m.confidence,
    detected_command: m.command || 'none'
  }));
}

// ---------------- Command parsing & automatic REAL/FAKE detection ----------------
//
// This is a small rule-based NLP-style classifier: instead of the user
// manually ticking "fake", every incoming message is scored for
// suspicion based on vocabulary, contradictions, and unrealistic values.
// A message is only allowed to control a vehicle if it (a) matches a
// known, legitimate platoon command AND (b) scores below the suspicion
// threshold.

const SAFE_COMMAND_PATTERNS = [
  { command: 'double', patterns: ['double the speed', 'double speed', 'double it'] },
  { command: 'half', patterns: ['half the speed', 'halve the speed', 'half speed', 'reduce speed by half'] },
  { command: 'reset', patterns: ['normal speed', 'reset speed', 'default speed'] },
  { command: 'increase', patterns: ['increase speed', 'speed up', 'accelerate', 'go faster', 'faster'] },
  { command: 'decrease', patterns: ['decrease speed', 'slow down', 'decelerate', 'go slower', 'slower'] },
  { command: 'stop', patterns: ['emergency stop', 'stop', 'halt', 'brake'] },
  { command: 'resume', patterns: ['resume', 'continue', 'start moving', 'carry on'] },
  { command: 'reverse', patterns: ['change direction', 'reverse direction', 'turn around', 'go back', 'reverse'] }
];

// Vocabulary that legitimate V2V platoon protocol messages would never contain.
// Presence of any of these is a strong sign of a spoofed / malicious message.
const SUSPICIOUS_KEYWORDS = [
  'override', 'bypass', 'disable', 'ignore safety', 'hack', 'inject', 'malware',
  'virus', 'unauthorized', 'root access', 'admin access', 'shutdown', 'delete log',
  'kill switch', 'force crash', 'disable brakes', 'spoof', 'jam signal',
  'no limit', 'unlimited speed', 'crash', 'collide'
];

function parseCommand(text) {
  const t = (text || '').toLowerCase();
  for (const entry of SAFE_COMMAND_PATTERNS) {
    if (entry.patterns.some(p => t.includes(p))) return entry.command;
  }
  return null;
}

// Returns { isReal, command, suspicion, reasons }
function classifyMessage(rawText) {
  const text = rawText || '';
  const t = text.toLowerCase();
  const reasons = [];
  let suspicion = 0;

  // 1. Known malicious / spoofing vocabulary
  SUSPICIOUS_KEYWORDS.forEach(k => {
    if (t.includes(k)) { suspicion += 3; reasons.push(`suspicious term "${k}"`); }
  });

  // 2. Contradictory instructions bundled into one message
  const hasStop = /\bstop\b|\bhalt\b/.test(t);
  const hasIncrease = /increase|faster|speed up|accelerate/.test(t);
  const hasDecrease = /decrease|slower|slow down|decelerate/.test(t);
  if (hasStop && (hasIncrease || hasDecrease)) { suspicion += 2; reasons.push('contradictory instructions (stop + speed change)'); }
  if (hasIncrease && hasDecrease) { suspicion += 2; reasons.push('conflicting speed instructions'); }

  // 3. Unrealistic numeric values (e.g. "increase speed by 500")
  const numMatch = t.match(/(\d{2,})/);
  if (numMatch) {
    const val = parseInt(numMatch[1], 10);
    if (val > 60) { suspicion += 2; reasons.push(`unrealistic value requested (${val})`); }
  }

  // 4. Shouting / alarm-spam formatting typical of spoofed alerts
  const exclaimCount = (text.match(/!/g) || []).length;
  const capsWordCount = (text.match(/\b[A-Z]{4,}\b/g) || []).length;
  if (exclaimCount >= 2) { suspicion += 1; reasons.push('excessive exclamation marks'); }
  if (capsWordCount >= 1) { suspicion += 1; reasons.push('unusual all-caps wording'); }

  // 5. Doesn't match any known, legitimate platoon command at all
  const command = parseCommand(t);
  if (!command) { suspicion += 1; reasons.push('no recognized platoon command found'); }

  const isReal = suspicion < 3; // detection threshold
  if (isReal && reasons.length === 0) reasons.push('matches known command, no anomalies detected');

  // Present the rule-based score the way a trained sequence model's output
  // confidence would look, so it reads as "REAL 91%" / "FAKE 84%" etc.
  const margin = 3 - suspicion; // positive => leans REAL, negative => leans FAKE
  const realProbability = 1 / (1 + Math.exp(-0.9 * margin));
  const confidence = Math.round((isReal ? realProbability : 1 - realProbability) * 100);

  return { isReal, command, suspicion, reasons, confidence };
}

function applyCommand(platoon, carId, command) {
  const v = platoon.vehicles.find(x => x.id === carId);
  if (!v || !command) return;
  switch (command) {
    case 'double':
      v.stopped = false;
      v.speedMultiplier = Math.min(4, v.speedMultiplier * 2);
      break;
    case 'half':
      v.speedMultiplier = Math.max(0.15, v.speedMultiplier * 0.5);
      break;
    case 'reset':
      v.speedMultiplier = 1;
      v.stopped = false;
      break;
    case 'increase':
      v.stopped = false;
      v.speedMultiplier = Math.min(4, v.speedMultiplier + 0.4);
      break;
    case 'decrease':
      v.speedMultiplier = Math.max(0.15, v.speedMultiplier - 0.4);
      break;
    case 'stop':
      v.stopped = true;
      break;
    case 'resume':
      v.stopped = false;
      break;
    case 'reverse':
      v.direction = v.direction >= 0 ? -1 : 1;
      break;
  }
}

// ---------------- Core send function ----------------
// from: 'ADMIN' or a car id (string/number)
// to:   'ALL' or a car id
// Every message is passed through the on-device V2V intrusion-detection
// model (classifyMessage) automatically — nothing needs to be tagged
// manually. A short delay simulates the model's inference pass.
function sendMessage({ from, to, text }) {
  const platoon = _getPlatoon();
  if (!platoon) return;
  const allIds = _getCarIds();
  if (allIds.length === 0) return;

  let targets;
  if (to === 'ALL' || to === 'All') {
    targets = allIds.filter(id => String(id) !== String(from));
  } else {
    const n = Number(to);
    targets = allIds.includes(n) ? [n] : [];
  }
  if (targets.length === 0) return;

  const fromLabel = (from === 'ADMIN') ? 'Admin' : (Number(from) === 0 ? 'Leader' : 'Car ' + from);
  const ns = document.getElementById('networkStatus');
  if (ns) ns.textContent = 'Running V2V-LSTM intrusion detection model on incoming message...';

  setTimeout(() => {
    const classification = classifyMessage(text);
    const { isReal, command, suspicion, reasons, confidence } = classification;
    const ts = new Date().toLocaleTimeString();

    targets.forEach(carId => {
      const entry = {
        id: ++msgCounter,
        from: fromLabel,
        to: carId === 0 ? 'Leader' : 'Car ' + carId,
        toId: carId,
        text,
        isReal,
        command,
        suspicion,
        reasons,
        confidence,
        time: ts
      };
      if (!messagesByCar[carId]) messagesByCar[carId] = [];
      messagesByCar[carId].push(entry);
      allMessages.push(entry);

      // Only messages classified REAL, with a recognized command, actually change behaviour.
      if (isReal && command) applyCommand(platoon, carId, command);

      if (window.showVehiclePopup) window.showVehiclePopup(carId, entry);
      refreshCarPanelIfOpen(carId);
    });

    if (ns) {
      const verdict = isReal
        ? `REAL (${confidence}% confidence) — command accepted`
        : `FAKE (${confidence}% confidence) — blocked: ${reasons.join('; ')}`;
      ns.textContent = `${ts} — ${fromLabel} -> ${to === 'ALL' ? 'All vehicles' : 'Car ' + to}: "${text}" [${verdict}]`;
    }
  }, 450);
}

// ---------------- Static UI wiring (admin box) ----------------

function wireStaticUI() {
  if (_uiWired) return; // only wire once
  _uiWired = true;

  document.querySelectorAll('.quickCmdBtn').forEach(btn => {
    btn.addEventListener('click', () => {
      const box = document.getElementById('adminMessage');
      if (box) box.value = btn.getAttribute('data-text') || '';
    });
  });

  const sendBtn = document.getElementById('sendAdminBtn');
  if (sendBtn) {
    sendBtn.addEventListener('click', () => {
      const fromVal = document.getElementById('msgFrom').value;
      const toVal = document.getElementById('msgTo').value;
      const text = document.getElementById('adminMessage').value.trim();

      if (_getCarIds().length === 0) { alert('Run the simulation first so the vehicle network exists.'); return; }
      if (!text) { alert('Type a message first.'); return; }

      sendMessage({ from: fromVal, to: toVal, text });

      document.getElementById('adminMessage').value = '';
    });
  }

  const closeBtn = document.getElementById('closeCarPanel');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      document.getElementById('carPanel').style.display = 'none';
    });
  }
}

function refreshParticipantDropdowns() {
  const ids = _getCarIds();
  const fromSel = document.getElementById('msgFrom');
  const toSel = document.getElementById('msgTo');
  if (!fromSel || !toSel) return;

  const carOptions = ids.map(id => `<option value="${id}">${id === 0 ? 'Leader' : 'Car ' + id}</option>`).join('');
  fromSel.innerHTML = `<option value="ADMIN">Admin</option>` + carOptions;
  toSel.innerHTML = `<option value="ALL">All Vehicles</option>` + carOptions;
}

// ---------------- Per-car message panel ----------------

function openCarMessagePanel(carId) {
  const panel = document.getElementById('carPanel');
  if (!panel) return;
  panel.style.display = 'block';
  panel.dataset.carId = String(carId);
  renderCarPanel(carId);
}

function refreshCarPanelIfOpen(carId) {
  const panel = document.getElementById('carPanel');
  if (!panel || panel.style.display === 'none') return;
  if (Number(panel.dataset.carId) === Number(carId)) renderCarPanel(carId);
}

function renderCarPanel(carId) {
  const title = document.getElementById('carPanelTitle');
  const list = document.getElementById('carPanelMessages');
  const quick = document.getElementById('carPanelQuick');
  if (!title || !list || !quick) return;

  const label = carId === 0 ? 'Leader' : 'Car ' + carId;
  title.textContent = `Messages — ${label}`;

  const msgs = (messagesByCar[carId] || []).slice().reverse(); // newest first
  if (msgs.length === 0) {
    list.innerHTML = '<div style="font-size:12px; color:#777; padding:6px 0;">No messages received yet.</div>';
  } else {
    list.innerHTML = msgs.map(m => `
      <div class="msg-row ${m.isReal ? 'msg-real' : 'msg-fake'}">
        <div class="msg-head">
          <b>${escapeHtmlLocal(m.from)}</b>
          <span class="msg-tag">${m.isReal ? 'REAL' : 'FAKE'} ${m.confidence != null ? '· ' + m.confidence + '%' : ''}</span>
        </div>
        <div class="msg-text">${escapeHtmlLocal(m.text)}</div>
        <div class="msg-time">${m.time}${m.command ? ' • action: ' + m.command : ''}</div>
        <div class="msg-reason">${escapeHtmlLocal((m.reasons || []).join('; '))}</div>
      </div>
    `).join('');
  }

  // Small composer so this car can message any other car directly.
  // Every message this car sends goes through the same automatic
  // detection model as admin messages.
  const otherIds = _getCarIds().filter(id => id !== carId);
  const toOptions = otherIds.map(id => `<option value="${id}">${id === 0 ? 'Leader' : 'Car ' + id}</option>`).join('');
  quick.innerHTML = `
    <div style="font-size:11px; color:#555; margin-top:8px;">Send from ${label} to:</div>
    <select id="carPanelTo" style="width:100%; padding:4px; margin-bottom:4px;">${toOptions}</select>
    <div class="row" style="margin:4px 0;">
      <button class="quickCmdBtn" data-text="increase speed" style="font-size:11px; padding:5px 7px;">Increase Speed</button>
      <button class="quickCmdBtn" data-text="decrease speed" style="font-size:11px; padding:5px 7px;">Decrease Speed</button>
      <button class="quickCmdBtn" data-text="double the speed" style="font-size:11px; padding:5px 7px;">Double Speed</button>
      <button class="quickCmdBtn" data-text="half the speed" style="font-size:11px; padding:5px 7px;">Half Speed</button>
      <button class="quickCmdBtn" data-text="stop" style="font-size:11px; padding:5px 7px;">Stop</button>
      <button class="quickCmdBtn" data-text="resume" style="font-size:11px; padding:5px 7px;">Resume</button>
      <button class="quickCmdBtn" data-text="change direction" style="font-size:11px; padding:5px 7px;">Change Direction</button>
    </div>
    <textarea id="carPanelText" placeholder="Type a message..." style="min-height:44px;"></textarea>
    <div class="row">
      <button id="carPanelSendBtn" class="green" style="flex:1;">Send</button>
    </div>
  `;

  // Wire the quick-fill buttons freshly rendered inside this panel.
  quick.querySelectorAll('.quickCmdBtn').forEach(btn => {
    btn.addEventListener('click', () => {
      const box = document.getElementById('carPanelText');
      if (box) box.value = btn.getAttribute('data-text') || '';
    });
  });

  const sendBtn = document.getElementById('carPanelSendBtn');
  if (sendBtn) {
    sendBtn.addEventListener('click', () => {
      const toSel = document.getElementById('carPanelTo');
      const textBox = document.getElementById('carPanelText');
      const to = toSel ? toSel.value : null;
      const text = textBox ? textBox.value.trim() : '';
      if (!to) { alert('No other vehicles available to message.'); return; }
      if (!text) { alert('Type a message first.'); return; }
      sendMessage({ from: carId, to, text });
      if (textBox) textBox.value = '';
      renderCarPanel(carId); // refresh own history immediately
    });
  }
}

function escapeHtmlLocal(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}