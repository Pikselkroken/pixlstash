// First-run setup screen. Collects the library folder and (when a discrete GPU
// is detected) a CPU/GPU choice, then commits them via the preload bridge. On
// success the main process boots the backend and navigates this window to the
// library, so a successful commit() never returns here.
/* global window, document */
const api = window.pixlstashDesktop;

const els = {
  folder: document.getElementById('folder'),
  pick: document.getElementById('pick'),
  imported: document.getElementById('imported'),
  importedText: document.getElementById('importedText'),
  computePanel: document.getElementById('computePanel'),
  computeOptions: document.getElementById('computeOptions'),
  start: document.getElementById('start'),
  error: document.getElementById('error'),
  progress: document.getElementById('progress'),
  barFill: document.getElementById('barFill'),
  caption: document.getElementById('progressCaption'),
};

let gpu = { available: false };
let busy = false;

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

function selectedUseGpu() {
  const checked = els.computeOptions.querySelector('input[name="compute"]:checked');
  return checked ? checked.value === 'gpu' : false;
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
      els.computeOptions
        .querySelectorAll('.choice')
        .forEach((c) => c.classList.remove('selected'));
      if (radio.checked) wrap.classList.add('selected');
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

async function init() {
  const p = await api.probeSetup();
  els.folder.value = p.defaults.imageRoot || '';
  if (p.importedFrom) {
    els.importedText.textContent = `Imported your existing settings from ${p.importedFrom}.`;
    show(els.imported);
  }
  gpu = p.gpu || { available: false };
  if (gpu.available) {
    renderCompute(Boolean(p.defaults.useGpu));
    show(els.computePanel);
  }
}

els.pick.addEventListener('click', async () => {
  if (busy) return;
  const dir = await api.pickLibraryFolder(els.folder.value);
  if (dir) els.folder.value = dir;
});

els.start.addEventListener('click', async () => {
  if (busy) return;
  hide(els.error);
  const imageRoot = els.folder.value.trim();
  if (!imageRoot) {
    showError('Please choose a library folder.');
    return;
  }
  busy = true;
  els.start.disabled = true;
  els.pick.disabled = true;
  els.start.textContent = 'Setting up…';
  try {
    await api.commitSetup({ imageRoot, useGpu: gpu.available && selectedUseGpu() });
    // Success → main process navigates this window to the library.
  } catch (e) {
    showError((e && e.message) || String(e));
    busy = false;
    els.start.disabled = false;
    els.pick.disabled = false;
    els.start.textContent = 'Get started';
  }
});

api.onProgress((p) => {
  show(els.progress);
  // pip emits no byte-level progress over a pipe (its bar is TTY-only), so most
  // of the install reports an unknown fraction — show an animated indeterminate
  // bar for those rather than a misleading full one.
  const known = p.fraction >= 0;
  els.barFill.classList.toggle('indeterminate', !known);
  els.barFill.style.width = known ? `${Math.round(p.fraction * 100)}%` : '';
  els.caption.textContent = p.message || 'Working…';
});

init().catch((e) => showError((e && e.message) || String(e)));
