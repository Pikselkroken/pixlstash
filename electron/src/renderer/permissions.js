// Permission-recovery screen. The backend refused to start because a folder
// holding the hub or the library is writable by other local accounts; it named
// every path it can safely tighten. This screen shows that list and reports the
// user's one decision back to main, which either retries the backend once with
// the repair authorised or quits. A successful fix navigates this window to the
// library, so resolve(true) usually never returns here.
/* global window, document */
const api = window.pixlstashDesktop;

const els = {
  issues: document.getElementById('issues'),
  fix: document.getElementById('fix'),
  quit: document.getElementById('quit'),
  error: document.getElementById('error'),
};

let answered = false;

function row(issue) {
  const item = document.createElement('li');
  item.className = 'repair-item';

  const area = document.createElement('div');
  area.className = 'repair-area';
  area.textContent = issue.area;

  const path = document.createElement('code');
  path.className = 'repair-path';
  path.textContent = issue.path;

  const change = document.createElement('div');
  change.className = 'repair-change';
  // "Open to everyone 777" reads as the problem; the octal is for the person
  // who already knows what it means, not the person who does not.
  change.textContent = `Permissions ${issue.current_mode} → ${issue.repaired_mode}`;

  item.append(area, path, change);
  return item;
}

function answer(accepted) {
  if (answered) return;
  answered = true;
  els.fix.disabled = true;
  els.quit.disabled = true;
  api.resolvePermissionRepair(accepted);
}

async function render() {
  let request = null;
  try {
    request = await api.permissionRepairRequest();
  } catch (err) {
    // Main always has the request by the time this page loads; a failure here
    // means the bridge itself is broken, so say so rather than showing an
    // empty, meaningless "Fix it".
    els.error.textContent = `Could not read the permission report: ${err && err.message ? err.message : err}`;
    els.error.classList.remove('hidden');
    els.fix.disabled = true;
    return;
  }
  const issues = (request && request.issues) || [];
  for (const issue of issues) els.issues.append(row(issue));
  if (!issues.length) els.fix.disabled = true;
}

els.fix.addEventListener('click', () => answer(true));
els.quit.addEventListener('click', () => answer(false));
// Escape is the conventional "no" on a modal decision, and this window has no
// other way out: the title bar's close button quits the app either way.
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') answer(false);
});

void render();
