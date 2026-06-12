// First-run / splash controller. Talks to the main process via the locked-down
// window.pixlstashDesktop bridge (see preload.ts). No framework, no build step.
//
// The bundled CPU/Metal runtime always boots straight into the library, so this
// is just a status splash. GPU acceleration is managed in the Backends window
// (Cmd/Ctrl+Shift+R), not here.
/* global window, document */
const api = window.pixlstashDesktop;

const els = {
  busy: document.getElementById('busy'),
  status: document.getElementById('status'),
  error: document.getElementById('error'),
};

function show(el) {
  el.classList.remove('hidden');
}
function hide(el) {
  el.classList.add('hidden');
}

function setBusy(text) {
  show(els.busy);
  els.status.textContent = text;
  hide(els.error);
}

function showError(message) {
  hide(els.busy);
  els.status.textContent = 'Something went wrong';
  show(els.error);
  els.error.textContent = message;
}

api.onPhase((p) => {
  switch (p.phase) {
    case 'detect':
      setBusy('Detecting hardware…');
      break;
    case 'starting':
      setBusy('Starting PixlStash…');
      break;
    case 'ready':
      setBusy('Loading library…');
      break;
    case 'error':
      showError(p.message);
      break;
    default:
      break;
  }
});
