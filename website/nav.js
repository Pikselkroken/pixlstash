(function () {
  // Apply the saved theme as early as possible (this script sits near the top of
  // <body>) to minimise a flash. Dark is the default "Quiet Darkroom" identity.
  try {
    if (localStorage.getItem("pixlstash-theme") === "light") {
      document.documentElement.setAttribute("data-theme", "light");
    }
  } catch (e) {
    /* localStorage unavailable, stay on the dark default */
  }

  const links = [
    { href: "index.html", label: "Home" },
    { href: "introduction.html", label: "Getting started" },
    { href: "features.html", label: "Features" },
    { href: "comfyui.html", label: "ComfyUI" },
    {
      href: "whatsnew.html",
      label: "New in v1.11",
      className: "nav-link--whatsnew",
    },
    { href: "api.html", label: "API" },
    {
      href: "https://github.com/pikselkroken/pixlstash",
      label: "GitHub",
      external: true,
    },
  ];

  const page = window.location.pathname.split("/").pop() || "index.html";

  function anchor(link) {
    const isActive = link.href === page;
    const ext = link.external
      ? ' target="_blank" rel="noopener noreferrer"'
      : "";
    const classes = ["nav-link"];
    if (link.className) classes.push(link.className);
    if (isActive) classes.push("nav-link--active");
    return `<a href="${link.href}" class="${classes.join(" ")}"${ext}>${link.label}</a>`;
  }

  // "Home" is a direct child of the nav so it stays beside the wordmark on
  // mobile; the rest become the scrolling second row.
  const home = links[0];
  const rest = links.slice(1);

  const moon =
    '<svg class="ic-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>';
  const sun =
    '<svg class="ic-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';

  const nav = document.createElement("nav");
  nav.className = "nav";
  nav.innerHTML = `
        <div class="brand">
          <img src="assets/logo.png" alt="PixlStash logo" />
          <span class="brand-name">Pixl<span class="brand-stash">Stash</span></span>
        </div>
        ${anchor(home)}
        <div class="nav-links">
          ${rest.map(anchor).join("\n          ")}
        </div>
        <div class="nav-actions">
          <button class="theme-toggle" id="themeToggle" type="button" aria-label="Toggle light / dark theme">${moon}${sun}</button>
          <a class="demo-btn" href="https://demo.pixlstash.dev?token=o75qQ-w0fy_FraPb2sdxcGOVTBoKFmmZwStycljomSs" target="_blank" rel="noopener noreferrer">Try demo</a>
          <a class="download-btn" href="install.html">Install now</a>
        </div>`;

  const placeholder = document.getElementById("nav-placeholder");
  if (placeholder) {
    placeholder.replaceWith(nav);
  }

  // Theme toggle.
  const root = document.documentElement;
  const btn = document.getElementById("themeToggle");
  if (btn) {
    const paint = (theme) => btn.classList.toggle("is-light", theme === "light");
    btn.addEventListener("click", () => {
      const next =
        root.getAttribute("data-theme") === "light" ? "dark" : "light";
      if (next === "light") {
        root.setAttribute("data-theme", "light");
      } else {
        root.removeAttribute("data-theme");
      }
      try {
        localStorage.setItem("pixlstash-theme", next);
      } catch (e) {
        /* ignore persistence failure */
      }
      paint(next);
    });
    paint(root.getAttribute("data-theme") === "light" ? "light" : "dark");

    // Consolidate the theme across open tabs: when another tab writes the
    // shared localStorage key, this tab's `storage` event fires, so apply it live
    // so every tab (and every page) stays on one setting. Uses web storage, no
    // cookies. (localStorage is shared per-origin; sessionStorage is per-tab and
    // would do the opposite, so it is deliberately not used here.)
    window.addEventListener("storage", (e) => {
      if (e.key !== "pixlstash-theme") return;
      const light = e.newValue === "light";
      if (light) {
        root.setAttribute("data-theme", "light");
      } else {
        root.removeAttribute("data-theme");
      }
      paint(light ? "light" : "dark");
    });
  }
})();
