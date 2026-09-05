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
// True once the runtime install has reported anything, which is also when the
// backend is already up reading the library behind it.
let reading = false;
// A commit that came back with an error. The install step keeps it on screen
// rather than dropping the user at the first question with nothing to read.
let failed = false;

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
    if (step === 'library' && els.folder.value) {
      row.querySelector('.step-value').title = els.folder.value;
    }
    els.steps.appendChild(row);
  });
  questionSteps().forEach((step, i) => {
    const cell = document.getElementById(`value-${step}`);
    if (!cell) return;
    const answer = i < at || (i === at && step === 'library') ? answerFor(step) : null;
    // The icon is a constant declared in this file, so it goes in as markup.
    // The label is not: for the library step it is the owner's own folder name,
    // and a folder may legally be called `<img src=x onerror=...>` on every
    // platform PixlStash runs on. It goes in as text, the way every other
    // dynamic value in this renderer already does (see `renderLines`).
    cell.innerHTML = answer ? answer.icon : '';
    if (answer) {
      const label = document.createElement('span');
      label.textContent = answer.text;
      cell.appendChild(label);
    }
  });
}

/**
 * The answer shown beside a completed step, as `{icon, text}` or null.
 *
 * Split rather than returned as one HTML string so the caller can put the icon
 * in as markup and the label in as text. Returning markup made the folder name
 * an HTML injection point.
 */
function answerFor(step) {
  if (step === 'library') {
    if (!mode) return null;
    const icon = mode === 'open' && inspection && inspection.isLibrary ? LOGO : FOLDER_ICON;
    // The folder's own name, clipped by CSS when even that is long. The whole
    // path is on the row, because the name alone cannot tell two "Pictures"
    // apart.
    return { icon, text: basename(els.folder.value) };
  }
  if (step === 'compute') {
    return { icon: CHIP_ICON, text: selectedComputeLabel() };
  }
  if (step === 'privacy') {
    const opt = PRIVACY_OPTIONS[privacyVariant].find((o) => o.key === privacyChoice);
    return opt ? { icon: barsMark(opt.bars), text: opt.name } : null;
  }
  return null;
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
  // On the install step there is nothing to press - unless it failed, in which
  // case both controls come back: one to change an answer, one to try again.
  els.back.classList.toggle('off', at === 0 || busy || (currentStep() === 'install' && !failed));
  els.next.classList.toggle('off', currentStep() === 'install' && !failed);
  els.next.textContent = failed
    ? 'Try again'
    : at === steps.length - 2
      ? 'Get started'
      : 'Continue';
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

  // Nothing suggested, nothing to say about it yet: ask for the folder rather
  // than passing judgement on an empty field.
  if (!els.folder.value) {
    els.next.disabled = true;
    els.hint.textContent = 'Choose the folder your pictures are in.';
    setVerdict({
      tone: 'ok',
      mark: '&middot;',
      title: 'No folder chosen yet',
      sub: 'PixlStash says what it found there before anything is written.',
      stats: [],
    });
    return;
  }

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
    // A vault.db proves a library was made here, not that anything is in it.
    // Prefer what the library itself says; fall back to the walk when it could
    // not be read.
    const inLibrary = inspection.library ? inspection.library.pictures : inspection.pictureCount;
    if (!inLibrary) {
      setVerdict({
        tone: 'warn',
        mark: LOGO,
        titleHtml: `${WORDMARK} library found here, and it is empty`,
        sub: 'Nothing has ever been added to it. Open it anyway, or choose the folder your pictures are actually in.',
        stats: [['0', 'Pictures'], ...free],
      });
      return;
    }
    setVerdict({
      tone: 'ok',
      mark: LOGO,
      titleHtml: `${WORDMARK} library found here`,
      sub: 'Tags, people and scores come back with it. Nothing is re-imported.',
      stats: inspection.library
        ? [
            [count(inspection.library.pictures), 'Pictures'],
            [count(inspection.library.people), 'People'],
            [count(inspection.library.tags), 'Tags'],
            ...free,
          ]
        : [
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
  // Only "start empty" gets a path suggested for it. A folder someone already
  // has is theirs to name, and a prefill nobody chose is how the wrong one gets
  // accepted.
  els.folder.value =
    next === 'open'
      ? detectedLegacyIdentitySource || defaults.existingRoot || ''
      : defaults.newRoot || '';
  inspection = null;
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

// The install screen is a FIXED set of lines, decided from the answers before
// anything runs. Never one line per event: pip reports a line per package it
// fetches, and keying rows by message grew a list a screen long. There are two
// things happening, so there are two lines, and the messages are the note on
// the line they belong to.
let lines = [];

function renderLines() {
  els.phases.innerHTML = '';
  for (const line of lines) {
    const item = document.createElement('li');
    // Only the line that carries the installer's own words reserves room for
    // them; the other would sit on three blank lines it never uses.
    item.className = line.notes ? 'phase phase--notes' : 'phase';
    item.dataset.state = line.state;
    item.innerHTML =
      '<span class="pname"></span><span class="pvalue"></span>' +
      '<div class="meter"><div class="meter-fill"></div></div>' +
      '<span class="pnote"></span>';
    item.querySelector('.pname').textContent = line.name;
    item.querySelector('.pvalue').textContent = line.value || '';
    item.querySelector('.pnote').textContent = line.note;

    const meter = item.querySelector('.meter');
    const fill = item.querySelector('.meter-fill');
    const known = line.fraction >= 0;
    if (line.state === 'done') {
      // A DOM style property, not a style attribute: the attribute is what the
      // window's CSP refuses.
      fill.style.width = '100%';
    } else if (known) {
      fill.style.width = `${Math.round(Math.min(1, line.fraction) * 100)}%`;
    } else {
      // No fill at all when the amount is unknown: a hairline crossing the
      // track says "working" without claiming a percentage.
      meter.classList.add('meter--unknown');
    }
    els.phases.appendChild(item);
  }
}

function setLine(id, patch) {
  const line = lines.find((l) => l.id === id);
  if (!line) return;
  Object.assign(line, patch);
  renderLines();
}

/** The lines this setup will have, decided once from the answers. */
function planLines(useGpu) {
  lines = [
    {
      id: 'server',
      name: 'Preparing your library',
      note: '',
      value: '',
      state: 'running',
      fraction: -1,
    },
  ];
  if (useGpu) {
    lines.push({
      id: 'runtime',
      name: `Installing ${gpu.label || 'the GPU runtime'}`,
      note: '',
      value: '',
      notes: true,
      state: 'running',
      fraction: -1,
    });
  }
  renderLines();
}

async function commit() {
  if (busy) return;
  busy = true;
  failed = false;
  hide(els.error);
  go(steps.indexOf('install'));
  els.next.disabled = true;
  const useGpu = gpu.available && selectedUseGpu();
  planLines(useGpu);
  try {
    await api.commitSetup({
      imageRoot: els.folder.value.trim(),
      useGpu,
      installLocation: els.installPath.value.trim(),
      importLegacyIdentity:
        !els.legacyIdentityPanel.classList.contains('hidden') && els.importLegacyIdentity.checked,
      telemetry: privacyPatch(),
    });
    // Success → main process navigates this window to the library.
  } catch (e) {
    // Stay on the step that failed and say why. Dropping back to the first
    // question hid the message on a screen that was no longer shown, which is
    // how "the backend refuses this folder's permissions" reached the user as
    // an unexplained trip back to the beginning.
    busy = false;
    failed = true;
    els.next.disabled = false;
    lines = [
      {
        id: 'failed',
        name: 'Setup could not finish',
        note: '',
        value: '',
        state: 'todo',
        fraction: -1,
      },
    ];
    renderLines();
    showError((e && e.message) || String(e));
    render();
    els.hint.textContent = 'Change an answer, or try again.';
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

els.back.addEventListener('click', () => {
  if (busy) return;
  hide(els.error);
  failed = false;
  lines = [];
  renderLines();
  go(at - 1);
});

els.next.addEventListener('click', () => {
  if (busy || els.next.disabled) return;
  if (failed || at >= steps.length - 2) commit();
  else go(at + 1);
});

// The installer's own last word, on the runtime's line only. It must never
// touch the reading line: the whole point of starting the server first is that
// the two run at once, and one line overwriting the other hides that.
api.onProgress((p) => {
  if (!busy) return;
  // pip names each wheel's size as it starts it and says nothing more until the
  // next one, so bytes fetched against bytes named is the honest measure - the
  // per-file fraction it sometimes reports is progress through one wheel, not
  // through the download.
  const total = Number(p.bytesTotal) || 0;
  const done = Number(p.bytesDone) || 0;
  setLine('runtime', {
    state: 'running',
    note: p.message || '',
    value: total ? `${humanBytes(done)} of ${humanBytes(total)}` : '',
    fraction: total ? done / total : -1,
  });
});

// The shell's own phases. 'starting' before the reading has begun is the first
// backend coming up; after it, the restart onto the GPU runtime.
api.onPhase((p) => {
  if (!busy) return;
  if (p.phase === 'reading') {
    reading = true;
    if (p.failed) {
      // The read could not start. Say so on its own line rather than leaving a
      // bar moving forever over work that is not happening: the app reads the
      // folder itself when it opens, which is what used to happen anyway.
      setLine('server', {
        name: 'Reading your pictures',
        state: 'todo',
        value: 'When PixlStash opens',
        fraction: -1,
      });
      return;
    }
    // The read counts folders while it walks them and pictures while it looks
    // for faces, so the line names what it is counting. Calling both "folders"
    // is how a count of 153 pictures ended up labelled as folders.
    const noun = p.stage === 'faces' ? 'pictures' : 'folders';
    const counted = p.total ? `${count(p.processed)} of ${count(p.total)} ${noun}` : '';
    setLine('server', {
      name: 'Reading your pictures',
      state: 'running',
      value: counted,
      fraction: typeof p.fraction === 'number' && p.fraction >= 0 ? p.fraction : -1,
    });
  } else if (p.phase === 'starting') {
    if (reading) {
      setLine('runtime', { state: 'done', note: '', value: 'Installed', fraction: 1 });
      setLine('server', {
        name: 'Starting PixlStash on your GPU',
        state: 'running',
        note: '',
        value: '',
        fraction: -1,
      });
    } else {
      setLine('server', { name: 'Starting PixlStash', state: 'running' });
    }
  } else if (p.phase === 'error' && p.message) {
    showError(String(p.message));
  }
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

// The tour advances on its own while the install runs. Four slides, four
// seconds each: long enough to read a caption, short enough that a CUDA
// download is never watching the same one twice in a row.
const slides = document.querySelectorAll('.slide');
const dots = document.querySelectorAll('.dots i');
let slide = 0;
setInterval(() => {
  slide = (slide + 1) % slides.length;
  slides.forEach((el, i) => el.classList.toggle('is-on', i === slide));
  dots.forEach((el, i) => el.classList.toggle('is-on', i === slide));
}, 4000);

init().catch((e) => {
  steps = ['install'];
  at = 0;
  render();
  showError((e && e.message) || String(e));
});
