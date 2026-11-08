// Missing-library recovery screen. The backend refused to open the active
// library because its folder or database is gone, unreadable, or a different
// library - and it was right to, since opening it would create an empty
// database where the real one belongs. This screen says which case it is and
// offers every way out. Main does the work: each action either comes back
// with a message to show here, or ends this page in a relaunch.
/* global window, document */
const api = window.pixlstashDesktop;

const els = {
  title: document.getElementById('title'),
  intro: document.getElementById('intro'),
  cause: document.getElementById('cause'),
  reason: document.getElementById('reason'),
  notice: document.getElementById('notice'),
  error: document.getElementById('error'),
  retry: document.getElementById('retry'),
  find: document.getElementById('find'),
  others: document.getElementById('others'),
  alternatives: document.getElementById('alternatives'),
  create: document.getElementById('create'),
  createNote: document.getElementById('create-note'),
  quit: document.getElementById('quit'),
};

// Worded per case: "the folder is gone" and "the folder is there but empty"
// send somebody looking in different places.
const COPY = {
  folder_missing: {
    title: 'Your library could not be found',
    cause:
      'The folder is not there. It may have been deleted, moved or renamed, or it is on a drive that is not connected right now.',
  },
  vault_missing: {
    title: 'Your library could not be found',
    cause:
      'The folder is there, but the library inside it is not. It may have been emptied or recreated, or it is where a drive that is not connected should be.',
  },
  unreadable: {
    title: 'Your library could not be opened',
    cause:
      'The library is there, but PixlStash could not read it. This is usually a permissions, locking or disk problem - another program may be holding the file.',
  },
  foreign: {
    title: 'A different library is in your library’s folder',
    cause:
      'The folder holds a library, but not this one. It may have been replaced or copied over. PixlStash did not open it, so nothing in it changed.',
  },
};

let busy = false;

function show(el, text) {
  el.textContent = text || '';
  el.classList.toggle('hidden', !text);
}

function setBusy(value) {
  busy = value;
  for (const button of document.querySelectorAll('button')) {
    if (button.closest('.winbar')) continue;
    button.disabled = value;
  }
}

// Every action goes through here: at most one at a time, and a refusal is
// shown on this page rather than ending it.
async function act(run) {
  if (busy) return;
  setBusy(true);
  show(els.error, '');
  try {
    const result = await run();
    if (result && result.ok) return; // main is relaunching; this page is done
    if (result && result.message) show(els.error, result.message);
  } catch (err) {
    show(els.error, err && err.message ? err.message : String(err));
  }
  setBusy(false);
}

function alternativeRow(library) {
  const item = document.createElement('li');
  item.className = 'repair-item recovery-alternative';

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary';
  button.textContent = `Open ${library.name}`;
  button.addEventListener('click', () => act(() => api.switchToLibrary(library.uuid)));

  const path = document.createElement('code');
  path.className = 'repair-path';
  path.textContent = library.path;

  item.append(button, path);
  return item;
}

async function render() {
  let request = null;
  try {
    request = await api.libraryRecoveryRequest();
  } catch (err) {
    show(els.error, `Could not read what went wrong: ${err && err.message ? err.message : err}`);
  }
  const report = request && request.report;
  if (!report) {
    // Without a report there is nothing safe to offer but trying again.
    els.find.disabled = true;
    els.create.disabled = true;
    return;
  }

  const copy = COPY[report.kind] || COPY.folder_missing;
  els.title.textContent = copy.title;
  document.title = `PixlStash: ${copy.title}`;
  els.intro.textContent = `PixlStash could not open “${report.library.name}”. It was expected in ${report.library.path}.`;
  els.cause.textContent = copy.cause;
  els.reason.textContent = report.reason || report.vault_path;
  els.createNote.textContent = `Start an empty library in another folder. “${report.library.name}” is set aside, not deleted, and can be added back later.`;
  show(els.notice, request.notice);

  const alternatives = report.alternatives || [];
  for (const library of alternatives) els.alternatives.append(alternativeRow(library));
  els.others.classList.toggle('hidden', alternatives.length === 0);
}

els.retry.addEventListener('click', () => act(() => api.retryLibrary()));
els.find.addEventListener('click', () => act(() => api.findMissingLibrary()));
els.create.addEventListener('click', () => act(() => api.createReplacementLibrary()));
els.quit.addEventListener('click', () => act(() => api.quitLibraryRecovery()));

void render();
