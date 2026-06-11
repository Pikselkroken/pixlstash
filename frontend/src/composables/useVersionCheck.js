import { ref, computed, watch, onMounted, onUnmounted, toValue } from "vue";

// "New version available" check, shared by the sidebar brand (browser) and the
// desktop title bar. Only one of those is the active owner at a time — the
// `enabled` flag no-ops the fetch/interval for the inactive one so the check
// never runs twice. `installType` and `checkForUpdates` may be refs or getters.

const appVersion = __APP_VERSION__;

// PEP 440-aware version comparison: treats rc/a/b/dev as pre-releases.
function parseVersion(v) {
  const m = String(v).match(
    /^(\d+)\.(\d+)\.(\d+)(?:(\.?(?:a|b|rc|dev))(\d+))?/i,
  );
  if (!m) return null;
  const preTag = m[4]?.toLowerCase().replace(/^\./, "");
  const preWeight = { dev: -4, a: -3, b: -2, rc: -1 }[preTag] ?? 0;
  return [Number(m[1]), Number(m[2]), Number(m[3]), preWeight, Number(m[5] || 0)];
}
function isRemoteNewer(current, remote) {
  const a = parseVersion(current);
  const b = parseVersion(remote);
  if (!a || !b) return false; // conservatively: don't advertise if we can't parse
  for (let i = 0; i < a.length; i++) {
    if (b[i] > a[i]) return true;
    if (b[i] < a[i]) return false;
  }
  return false;
}

// Telemetry endpoint. The install type lives in the PATH (not the query
// string) because Cloudflare zone analytics can only filter on path — see
// issue #402. The response body is identical across all buckets.
const LATEST_VERSION_BASE_URL = "https://pixlstash.dev/latest-version";
// Install-type buckets allowed in the telemetry path; anything else (or
// empty) collapses to "other". Detection must never block the check.
const TELEMETRY_INSTALL_BUCKETS = new Set(["docker", "pip", "electron", "other"]);
const UPDATE_PAGE_URL = "https://pixlstash.dev/upgrade.html";

const VERSION_CHECK_STORAGE_KEY = "pixlstash:lastVersionCheck";
const VERSION_CHECK_SECURITY_KEY = "pixlstash:lastSecurityLevel";
const VERSION_CHECK_DISMISSED_KEY = "pixlstash:dismissedUpdateVersion";
const VERSION_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;

export function useVersionCheck(installType, checkForUpdates, enabled = true) {
  const latestVersion = ref(null);
  const latestVersionUrl = ref(null);
  const latestSecurityLevel = ref(null);
  const updateDismissed = ref(
    localStorage.getItem(VERSION_CHECK_DISMISSED_KEY) === latestVersion.value,
  );

  const isEnabled = () => Boolean(toValue(enabled));

  const updateAvailable = computed(
    () => latestVersion.value && isRemoteNewer(appVersion, latestVersion.value),
  );

  const isHighSecurity = computed(() => {
    const level = latestSecurityLevel.value;
    return Boolean(level) && ["critical", "high"].includes(level.toLowerCase());
  });

  const securityUpdateTitle = computed(() => {
    if (!latestSecurityLevel.value) return undefined;
    return `v${latestVersion.value} includes a ${latestSecurityLevel.value}-severity security fix. Update as soon as possible.`;
  });

  function dismissUpdateAlert() {
    localStorage.setItem(VERSION_CHECK_DISMISSED_KEY, latestVersion.value);
    updateDismissed.value = true;
  }

  function checkForUpdatesNow() {
    if (!isEnabled()) return;
    const last = parseInt(
      localStorage.getItem(VERSION_CHECK_STORAGE_KEY) ?? "0",
      10,
    );
    const lastSecurity = localStorage.getItem(VERSION_CHECK_SECURITY_KEY) ?? "";
    const isHigh = ["critical", "high"].includes(lastSecurity.toLowerCase());
    // Bypass the 24h throttle when the last known release was a High/Critical
    // security patch so it re-checks (and re-shows) on every page load.
    if (Date.now() - last < VERSION_CHECK_INTERVAL_MS && !isHigh) return;

    const type = toValue(installType);
    const bucket = TELEMETRY_INSTALL_BUCKETS.has(type) ? type : "other";
    const url = `${LATEST_VERSION_BASE_URL}/${encodeURIComponent(appVersion)}/${bucket}.json`;
    fetch(url)
      .then((r) => r.json())
      .then((data) => {
        localStorage.setItem(VERSION_CHECK_STORAGE_KEY, String(Date.now()));
        localStorage.setItem(VERSION_CHECK_SECURITY_KEY, data?.security ?? "");
        const remote = data?.version;
        if (remote && isRemoteNewer(appVersion, remote)) {
          const dismissed = localStorage.getItem(VERSION_CHECK_DISMISSED_KEY);
          latestVersion.value = remote;
          latestVersionUrl.value = `${UPDATE_PAGE_URL}/?v=${encodeURIComponent(appVersion)}&i=${encodeURIComponent(type ?? "pip")}`;
          latestSecurityLevel.value = data?.security ?? null;
          updateDismissed.value = dismissed === remote;
        }
      })
      .catch((e) => {
        console.warn("Version update check failed:", e);
      });
  }

  let versionCheckInterval = null;
  function startVersionCheckInterval() {
    if (versionCheckInterval) return;
    versionCheckInterval = setInterval(() => {
      if (toValue(checkForUpdates) === true) {
        checkForUpdatesNow();
      }
    }, VERSION_CHECK_INTERVAL_MS);
  }
  function stopVersionCheckInterval() {
    if (versionCheckInterval) {
      clearInterval(versionCheckInterval);
      versionCheckInterval = null;
    }
  }

  watch(
    () => toValue(checkForUpdates),
    (val) => {
      if (!isEnabled()) return;
      if (val === true) {
        if (!latestVersion.value) {
          checkForUpdatesNow();
        }
        startVersionCheckInterval();
      } else {
        stopVersionCheckInterval();
      }
    },
  );

  onMounted(() => {
    // Fetch the latest version directly from pixlstash.dev when the user has
    // opted in. The watcher above covers the case where the prop resolves
    // after mount.
    if (!isEnabled()) return;
    if (toValue(checkForUpdates) === true) {
      checkForUpdatesNow();
      startVersionCheckInterval();
    }
  });

  onUnmounted(stopVersionCheckInterval);

  return {
    latestVersion,
    latestVersionUrl,
    latestSecurityLevel,
    updateAvailable,
    updateDismissed,
    isHighSecurity,
    securityUpdateTitle,
    checkForUpdatesNow,
    dismissUpdateAlert,
  };
}
