// The startup framework: the screen PixlStash puts in front of the app whenever
// it has to ask something before the library can be trusted to open.
//
// A launch runs a LIST OF STEPS, and main decides the list (`setup:probe` →
// `steps`). First run asks all of them; an upgrade that owes the user one new
// question asks only that one. Anything else that has to happen before the app
// loads - help, repair, a new consent - belongs here as another step id rather
// than as a dialog thrown over a half-loaded library.
//
// On a successful commit the main process boots the backend and navigates this
// window to the library, so commit() never returns here.
/* global window, document */
const api = window.pixlstashDesktop;

const STEP_LABELS = {
  library: 'Your pictures',
  compute: 'Compute',
  privacy: 'Privacy',
};

const els = {
  steps: document.getElementById('steps'),
  forms: document.querySelectorAll('.setup-form'),
  back: document.getElementById('back'),
  next: document.getElementById('next'),
  hint: document.getElementById('hint'),
  // library
  cardOpen: document.getElementById('cardOpen'),
  cardNew: document.getElementById('cardNew'),
  answer: document.getElementById('answer'),
  answerHw: document.getElementById('answerHw'),
  folder: document.getElementById('folder'),
  pick: document.getElementById('pick'),
  verdict: document.getElementById('verdict'),
  verdictTitle: document.getElementById('verdictTitle'),
  verdictSub: document.getElementById('verdictSub'),
  verdictStats: document.getElementById('verdictStats'),
  imported: document.getElementById('imported'),
  importedText: document.getElementById('importedText'),
  legacyIdentityPanel: document.getElementById('legacyIdentityPanel'),
  importLegacyIdentity: document.getElementById('importLegacyIdentity'),
  legacyIdentitySource: document.getElementById('legacyIdentitySource'),
  // compute
  computeOptions: document.getElementById('computeOptions'),
  installLocation: document.getElementById('installLocation'),
  installPath: document.getElementById('installPath'),
  pickInstall: document.getElementById('pickInstall'),
  // privacy
  privacyAsk: document.getElementById('privacyAsk'),
  privacyLede: document.getElementById('privacyLede'),
  teleOptions: document.getElementById('teleOptions'),
  // install
  phases: document.getElementById('phases'),
  error: document.getElementById('error'),
};

const SCREENS = {
  library: document.getElementById('screenLibrary'),
  compute: document.getElementById('screenCompute'),
  privacy: document.getElementById('screenPrivacy'),
  install: document.getElementById('screenInstall'),
};

const FOLDER_ICON =
  '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
  '<path d="M3 20V5h7l2 2h9v13z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
const CHIP_ICON =
  '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
  '<rect x="6" y="6" width="12" height="12" rx="2" stroke="currentColor" stroke-width="2"/>' +
  '<path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
const LOGO = '<img src="Logo.png" alt="" />';
const WORDMARK = '<span class="wm">Pixl<span>Stash</span></span>';

// The three answers to the privacy question, in the app's own words
// (frontend/src/components/dialogs/TelemetryConsentDialog.vue). `bars` is how
// many of the three marks are lit, which is the app's option mark.
const PRIVACY_OPTIONS = {
  fresh: [
    {
      key: 'none',
      bars: 1,
      name: 'No check',
      desc: "Nothing leaves your machine. You'll need to watch for security releases yourself.",
      patch: { check_for_updates: false, telemetry_send_install_id: false },
    },
    {
      key: 'check',
      bars: 2,
      name: 'Check for updates',
      desc: 'Sends your version and platform. Nothing else.',
      patch: { check_for_updates: true, telemetry_send_install_id: false },
    },
    {
      key: 'checkid',
      bars: 3,
      name: 'Check + random ID',
      desc: 'Adds a random number, so I can tell ten people using PixlStash once from one person using it ten times.',
      patch: { check_for_updates: true, telemetry_send_install_id: true },
    },
  ],
  // The upgrade case: update checks are already answered, so the only question
  // left is the random ID. Exactly that one question, nothing re-asked.
  upgrade: [
    {
      key: 'check',
      bars: 2,
      name: 'No thanks',
      desc: 'Your update checks carry on exactly as they are.',
      patch: { telemetry_send_install_id: false },
    },
    {
      key: 'checkid',
      bars: 3,
      name: 'Add the random number',
      desc: 'So I can tell ten people using PixlStash once from one person using it ten times.',
      patch: { telemetry_send_install_id: true },
    },
  ],
};

let steps = [];
let at = 0;
let busy = false;
let gpu = { available: false };
let detectedLegacyIdentitySource = null;
let mode = null;
let inspection = null;
let inspectSeq = 0;
let privacyVariant = 'fresh';
let privacyChoice = null;

function show(el) {
  el.classList.remove('hidden');
}
function hide(el) {
  el.classList.add('hidden');
}
function showError(msg) {
  els.error.textContent = msg;
  show(els.error);
}

/** Human-readable bytes, in the units a person reads a disk in. */
function humanBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = value >= 100 || unit === 0 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${units[unit]}`;
}

function count(n) {
  return Number(n || 0).toLocaleString();
}

function basename(path) {
  const parts = String(path || '').split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

// ---- the rail: one row per step, question on the left, your answer on the right

function questionSteps() {
  return steps.filter((step) => step !== 'install');
}

function renderSteps() {
  els.steps.innerHTML = '';
  questionSteps().forEach((step, i) => {
    const row = document.createElement('div');
    row.className = 'step';
    row.dataset.state = i === at ? 'current' : i < at ? 'done' : 'todo';
    row.innerHTML =
      `<span class="dot">${i + 1}</span>` +
      `<span class="step-label">${STEP_LABELS[step] || step}</span>` +
      `<span class="step-value" id="value-${step}"></span>`;
    els.steps.appendChild(row);
  });
  questionSteps().forEach((step, i) => {
    const cell = document.getElementById(`value-${step}`);
    if (!cell) return;
    cell.innerHTML = i < at || (i === at && step === 'library') ? answerFor(step) : '';
  });
}

function answerFor(step) {
  if (step === 'library') {
    if (!mode) return '';
    const icon = mode === 'open' && inspection && inspection.isLibrary ? LOGO : FOLDER_ICON;
    return `${icon}<span>${basename(els.folder.value)}</span>`;
  }
  if (step === 'compute') {
    return `${CHIP_ICON}<span>${selectedComputeLabel()}</span>`;
  }
  if (step === 'privacy') {
    const opt = PRIVACY_OPTIONS[privacyVariant].find((o) => o.key === privacyChoice);
    return opt ? `${barsMark(opt.bars)}<span>${opt.name}</span>` : '';
  }
  return '';
}

function barsMark(lit) {
  return `<span class="tele-mark tele-mark--${lit}"><i></i><i></i><i></i></span>`;
}

// ---- navigation

function currentStep() {
  return steps[at];
}

function render() {
  Object.entries(SCREENS).forEach(([id, el]) => {
    el.classList.toggle('off', id !== currentStep());
  });
  renderSteps();
  els.back.classList.toggle('off', at === 0 || currentStep() === 'install' || busy);
  els.next.classList.toggle('off', currentStep() === 'install');
  els.next.textContent = at === steps.length - 2 ? 'Get started' : 'Continue';
  els.hint.textContent = '';
  if (currentStep() === 'library') renderLibrary();
  if (currentStep() === 'privacy') els.next.disabled = privacyChoice === null;
  if (currentStep() === 'compute') els.next.disabled = false;
}

function go(index) {
  at = Math.max(0, Math.min(steps.length - 1, index));
  render();
}

// ---- the library step

function renderLibrary() {
  els.cardOpen.setAttribute('aria-pressed', String(mode === 'open'));
  els.cardNew.setAttribute('aria-pressed', String(mode === 'new'));

  if (!mode) {
    els.answer.classList.add('waiting');
    els.answerHw.textContent = 'The folder';
    els.folder.value = '';
    els.pick.disabled = true;
    els.next.disabled = true;
    els.hint.textContent = 'Pick one to carry on.';
    setVerdict({
      tone: 'ok',
      mark: '&middot;',
      title: 'Nothing chosen yet',
      sub: 'PixlStash says what it found there before anything is written.',
      stats: [],
    });
    return;
  }

  els.answer.classList.remove('waiting');
  els.answer.dataset.mode = mode;
  els.pick.disabled = false;
  els.hint.textContent = '';
  els.answerHw.textContent =
    mode === 'open' ? 'The folder your pictures are in' : 'Where the new library goes';
  renderVerdict();
}

function setVerdict({ tone, mark, title, titleHtml, sub, stats }) {
  els.verdict.dataset.tone = tone;
  els.verdict.querySelector('.vmark').innerHTML = mark;
  if (titleHtml) els.verdictTitle.innerHTML = titleHtml;
  else els.verdictTitle.textContent = title;
  els.verdictSub.textContent = sub;
  els.verdictStats.innerHTML = stats
    .map(([value, label]) => `<span class="stat"><b>${value}</b><span>${label}</span></span>`)
    .join('');
}

function renderVerdict() {
  const free = inspection && inspection.freeBytes ? [[humanBytes(inspection.freeBytes), 'Free space']] : [];
  els.next.disabled = false;

  if (!inspection) {
    setVerdict({
      tone: 'ok',
      mark: '&middot;',
      title: 'Looking…',
      sub: 'Reading what is in this folder.',
      stats: free,
    });
    return;
  }

  if (mode === 'new') {
    setVerdict({
      tone: 'ok',
      mark: '&#10003;',
      title: inspection.exists ? 'This folder is ready' : 'This folder will be created',
      sub: 'The database is the only thing PixlStash writes here.',
      stats: free,
    });
    return;
  }

  if (inspection.isLibrary) {
    setVerdict({
      tone: 'ok',
      mark: LOGO,
      titleHtml: `${WORDMARK} library found here`,
      sub: 'Tags, people and scores come back with it. Nothing is re-imported.',
      stats: [
        [count(inspection.pictureCount) + (inspection.truncated ? '+' : ''), 'Pictures'],
        [humanBytes(inspection.pictureBytes), 'On disk'],
        ...free,
      ],
    });
    return;
  }

  if (inspection.pictureCount > 0) {
    setVerdict({
      tone: 'ok',
      mark: '&#10003;',
      title: `${count(inspection.pictureCount)}${inspection.truncated ? '+' : ''} pictures found here`,
      sub: 'Read where they sit. Tagging starts once you are in.',
      stats: [
        [count(inspection.pictureCount) + (inspection.truncated ? '+' : ''), 'Pictures'],
        [humanBytes(inspection.pictureBytes), 'On disk'],
        ...free,
      ],
    });
    return;
  }

  els.next.disabled = true;
  els.hint.textContent = 'Choose a folder with pictures in it.';
  setVerdict({
    tone: 'warn',
    mark: '!',
    title: 'No pictures in this folder',
    sub: 'Choose another folder, or start empty here instead.',
    stats: free,
  });
}

async function inspect(path) {
  const seq = ++inspectSeq;
  inspection = null;
  renderVerdict();
  try {
    const result = await api.inspectSetupPath(path);
    if (seq !== inspectSeq) return;
    inspection = result;
  } catch (e) {
    if (seq !== inspectSeq) return;
    inspection = { exists: false, isLibrary: false, pictureCount: 0, pictureBytes: 0, freeBytes: 0 };
    console.error('Failed to inspect the chosen folder:', e);
  }
  renderVerdict();
  renderSteps();
  updateLegacyIdentityVisibility();
}

function chooseMode(next, defaults) {
  mode = next;
  els.folder.value =
    next === 'open'
      ? detectedLegacyIdentitySource || defaults.imageRoot || ''
      : defaults.imageRoot || '';
  renderLibrary();
  if (els.folder.value) inspect(els.folder.value);
}

function updateLegacyIdentityVisibility() {
  const matchesDetected =
    detectedLegacyIdentitySource && els.folder.value.trim() === detectedLegacyIdentitySource;
  if (matchesDetected) {
    show(els.legacyIdentityPanel);
    return;
  }
  els.importLegacyIdentity.checked = false;
  updateLegacyIdentitySelected();
  hide(els.legacyIdentityPanel);
}

function updateLegacyIdentitySelected() {
  els.legacyIdentityPanel.classList.toggle('panel--selected', els.importLegacyIdentity.checked);
}

// ---- the compute step

function selectedUseGpu() {
  const checked = els.computeOptions.querySelector('input[name="compute"]:checked');
  return checked ? checked.value === 'gpu' : false;
}

function selectedComputeLabel() {
  const selected = els.computeOptions.querySelector('.choice.selected .label');
  return selected ? selected.textContent : 'Built-in (CPU)';
}

// The install-location picker only matters when a GPU runtime will be downloaded,
// so reveal it exactly when GPU is the selected compute option.
function updateInstallLocationVisibility() {
  if (gpu.available && selectedUseGpu()) show(els.installLocation);
  else hide(els.installLocation);
}

function renderCompute(defaultUseGpu) {
  const options = [
    { value: 'cpu', label: 'Built-in (CPU)', sub: 'Works immediately. No download.' },
    {
      value: 'gpu',
      label: gpu.label || 'GPU acceleration',
      sub: `Faster tagging and search using ${gpu.name || 'your GPU'}. Downloads ~2.5 GB now.`,
    },
  ];
  els.computeOptions.innerHTML = '';
  for (const opt of options) {
    const isGpu = opt.value === 'gpu';
    const selected = isGpu === defaultUseGpu;
    const wrap = document.createElement('label');
    wrap.className = selected ? 'choice selected' : 'choice';

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'compute';
    radio.value = opt.value;
    radio.checked = selected;
    radio.addEventListener('change', () => {
      els.computeOptions.querySelectorAll('.choice').forEach((c) => c.classList.remove('selected'));
      if (radio.checked) wrap.classList.add('selected');
      updateInstallLocationVisibility();
    });

    const meta = document.createElement('div');
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = opt.label;
    const sub = document.createElement('div');
    sub.className = 'sub';
    sub.textContent = opt.sub;
    meta.appendChild(label);
    meta.appendChild(sub);

    wrap.appendChild(radio);
    wrap.appendChild(meta);
    els.computeOptions.appendChild(wrap);
  }
}

// ---- the privacy step

function renderPrivacy() {
  const upgrade = privacyVariant === 'upgrade';
  els.privacyAsk.textContent = upgrade ? 'One new thing' : 'What may PixlStash send?';
  els.privacyLede.textContent = upgrade
    ? 'You already answered the update question. You could help PixlStash improve by sending a random number alongside those checks. Nothing else about your setup changes either way.'
    : "PixlStash can check pixlstash.dev once a day for a new version. Several past releases fixed critical security bugs, so I'd suggest leaving this on. You could also help PixlStash improve by sending a random number alongside it.";

  els.teleOptions.innerHTML = '';
  els.teleOptions.classList.toggle('tele--two', PRIVACY_OPTIONS[privacyVariant].length === 2);
  for (const opt of PRIVACY_OPTIONS[privacyVariant]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tele-opt';
    button.setAttribute('role', 'radio');
    button.setAttribute('aria-checked', String(privacyChoice === opt.key));
    button.innerHTML =
      `${barsMark(opt.bars)}<span class="tele-name">${opt.name}</span>` +
      `<span class="tele-desc">${opt.desc}</span>`;
    button.addEventListener('click', () => {
      privacyChoice = opt.key;
      renderPrivacy();
      els.next.disabled = false;
      renderSteps();
    });
    els.teleOptions.appendChild(button);
  }
}

function privacyPatch() {
  const opt = PRIVACY_OPTIONS[privacyVariant].find((o) => o.key === privacyChoice);
  return opt ? { ...opt.patch, telemetry_consent_prompted: true } : null;
}

// ---- the install step

const PHASES = [];

function renderPhases() {
  els.phases.innerHTML = '';
  for (const phase of PHASES) {
    const item = document.createElement('li');
    item.className = 'phase';
    item.dataset.state = phase.state;
    item.innerHTML =
      `<span class="pmark" aria-hidden="true">${phase.state === 'done' ? '&#10003;' : '&#9679;'}</span>` +
      `<span class="pname"></span><span class="pnote"></span>`;
    item.querySelector('.pname').textContent = phase.name;
    item.querySelector('.pnote').textContent = phase.note || '';
    if (phase.state === 'running') {
      const bar = document.createElement('div');
      bar.className = 'bar';
      const fill = document.createElement('div');
      fill.className = phase.fraction >= 0 ? 'barfill' : 'barfill indeterminate';
      // A DOM style property, not a style attribute: the attribute is what the
      // window's CSP refuses.
      if (phase.fraction >= 0) fill.style.width = `${Math.round(phase.fraction * 100)}%`;
      bar.appendChild(fill);
      item.appendChild(bar);
    }
    els.phases.appendChild(item);
  }
}

function setPhase(name, patch) {
  const existing = PHASES.find((p) => p.name === name);
  if (existing) Object.assign(existing, patch);
  else PHASES.push({ name, state: 'running', fraction: -1, note: '', ...patch });
  renderPhases();
}

async function commit() {
  if (busy) return;
  busy = true;
  hide(els.error);
  go(steps.indexOf('install'));
  els.next.disabled = true;
  setPhase('Setting up PixlStash', { state: 'running', fraction: -1 });
  try {
    await api.commitSetup({
      imageRoot: els.folder.value.trim(),
      useGpu: gpu.available && selectedUseGpu(),
      installLocation: els.installPath.value.trim(),
      importLegacyIdentity:
        !els.legacyIdentityPanel.classList.contains('hidden') && els.importLegacyIdentity.checked,
      telemetry: privacyPatch(),
    });
    // Success → main process navigates this window to the library.
  } catch (e) {
    showError((e && e.message) || String(e));
    busy = false;
    go(0);
  }
}

// ---- wiring

els.cardOpen.addEventListener('click', () => !busy && chooseMode('open', probeDefaults));
els.cardNew.addEventListener('click', () => !busy && chooseMode('new', probeDefaults));

els.pick.addEventListener('click', async () => {
  if (busy) return;
  const dir = await api.pickLibraryFolder(els.folder.value);
  if (!dir) return;
  els.folder.value = dir;
  updateLegacyIdentityVisibility();
  inspect(dir);
});

els.importLegacyIdentity.addEventListener('change', updateLegacyIdentitySelected);

els.pickInstall.addEventListener('click', async () => {
  if (busy) return;
  const dir = await api.pickBackendLocation(els.installPath.value);
  if (dir) els.installPath.value = dir;
});

els.back.addEventListener('click', () => !busy && go(at - 1));

els.next.addEventListener('click', () => {
  if (busy || els.next.disabled) return;
  if (at >= steps.length - 2) commit();
  else go(at + 1);
});

api.onProgress((p) => {
  const known = p.fraction >= 0;
  setPhase(p.message || 'Working…', { state: 'running', fraction: known ? p.fraction : -1 });
});

let probeDefaults = {};

async function init() {
  const p = await api.probeSetup();
  probeDefaults = p.defaults || {};
  steps = Array.isArray(p.steps) && p.steps.length ? p.steps.slice() : ['library'];
  if (!steps.includes('install')) steps.push('install');
  privacyVariant = p.privacyVariant === 'upgrade' ? 'upgrade' : 'fresh';

  els.installPath.value = probeDefaults.installLocation || '';
  gpu = p.gpu || { available: false };
  if (gpu.available) {
    renderCompute(Boolean(probeDefaults.useGpu));
    updateInstallLocationVisibility();
  }

  if (p.importedFrom) {
    els.importedText.textContent = `Found existing server settings at ${p.importedFrom}.`;
    show(els.imported);
  }
  detectedLegacyIdentitySource = p.legacyIdentitySource || null;
  if (detectedLegacyIdentitySource) {
    els.legacyIdentitySource.textContent = detectedLegacyIdentitySource;
  }
  updateLegacyIdentityVisibility();
  updateLegacyIdentitySelected();

  renderPrivacy();
  render();
}

init().catch((e) => {
  steps = ['install'];
  at = 0;
  render();
  showError((e && e.message) || String(e));
});
