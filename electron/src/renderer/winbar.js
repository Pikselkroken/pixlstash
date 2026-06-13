/* global window, document, navigator */
// Injects the frameless-window title bar (a drag region + window controls) into
// the splash + setup pages and wires the controls to the Electron preload
// bridge. macOS keeps its native traffic lights instead of custom controls.
(function () {
  const host = document.getElementById('winbar');
  if (!host) return;
  const api = window.pixlstashDesktop;
  const isMac = /mac/i.test(navigator.platform || navigator.userAgent || '');

  const controls = isMac
    ? ''
    : '<span class="wb-controls">' +
      '<button class="wb-btn wb-min" type="button" aria-label="Minimize"><svg width="10" height="10" viewBox="0 0 10 10"><rect x="0" y="4.5" width="10" height="1" fill="currentColor"/></svg></button>' +
      '<button class="wb-btn wb-max" type="button" aria-label="Maximize"><svg width="10" height="10" viewBox="0 0 10 10"><rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor"/></svg></button>' +
      '<button class="wb-btn wb-close" type="button" aria-label="Close"><svg width="10" height="10" viewBox="0 0 10 10"><path d="M1,1 L9,9 M9,1 L1,9" stroke="currentColor" stroke-width="1.1"/></svg></button>' +
      '</span>';
  host.innerHTML = '<span class="wb-brand">PixlStash</span>' + controls;
  if (isMac) host.classList.add('winbar--mac');

  if (!api) return;
  const click = (sel, fn) => {
    const b = host.querySelector(sel);
    if (b) b.addEventListener('click', fn);
  };
  click('.wb-min', () => api.windowMinimize && api.windowMinimize());
  click('.wb-max', () => api.windowToggleMaximize && api.windowToggleMaximize());
  click('.wb-close', () => api.windowClose && api.windowClose());
})();
