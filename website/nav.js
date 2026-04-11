(function () {
  const links = [
    { href: "index.html", label: "Home" },
    { href: "introduction.html", label: "Getting started" },
    { href: "features.html", label: "Features" },
    { href: "api.html", label: "API" },
    {
      href: "https://github.com/pikselkroken/pixlstash",
      label: "GitHub",
      external: true,
    },
  ];

  const page = window.location.pathname.split("/").pop() || "index.html";

  const navLinksHTML = links
    .map((link) => {
      const isActive = link.href === page;
      const ext = link.external
        ? ' target="_blank" rel="noopener noreferrer"'
        : "";
      const cls = isActive ? ' class="nav-link--active"' : "";
      return `<a href="${link.href}"${ext}${cls}>${link.label}</a>`;
    })
    .join("\n          ");

  const nav = document.createElement("nav");
  nav.className = "nav";
  nav.innerHTML = `
        <div class="brand">
          <img src="assets/logo.png" alt="PixlStash logo" />
          <span class="brand-name">PixlStash</span>
        </div>
        <div class="nav-links">
          ${navLinksHTML}
        </div>
        <div class="nav-actions">
          <a class="download-btn" href="install.html">Install now</a>
        </div>`;

  const placeholder = document.getElementById("nav-placeholder");
  if (placeholder) {
    placeholder.replaceWith(nav);
  }
})();
