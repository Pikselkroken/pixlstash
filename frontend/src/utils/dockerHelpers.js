/**
 * Pure helper functions for building Docker volume mounts and restart commands.
 * These functions have no Vue dependencies and can be imported anywhere.
 */

export function normalizeFolderPath(value) {
  return String(value || "")
    .trim()
    .replace(/\/+$/, "");
}

export function padFolderIndex(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return String(Math.trunc(parsed)).padStart(3, "0");
}

export function shellSingleQuote(value) {
  return `'${String(value || "").replace(/'/g, `"'"'`)}'`;
}

export function buildDockerVolumeFlag(hostPath, containerPath, format = "linux") {
  const source = String(hostPath || "").trim();
  const target = String(containerPath || "").trim();
  if (format === "windows") {
    return `-v "${source}:${target}"`;
  }
  return `-v ${shellSingleQuote(`${source}:${target}`)}`;
}

export function deriveLabelFromHostPath(value) {
  const normalized = String(value || "")
    .trim()
    .replace(/[\\/]+$/, "");
  if (!normalized) return "";
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  if (parts.length === 0) return "";
  const leaf = parts[parts.length - 1];
  if (!leaf || /^[A-Za-z]:$/.test(leaf)) return "";
  return leaf;
}

export function hostPathPlaceholderFor(containerPath) {
  const leaf = String(containerPath || "")
    .replace(/\/+$/, "")
    .split("/")
    .filter(Boolean)
    .pop();
  return `/absolute/host/path/for-${leaf || "folder"}`;
}

export function inferImportMount(folder, fallbackIndex = null) {
  const rawFolder = normalizeFolderPath(folder?.folder);
  if (!rawFolder) return null;

  const storedHost = String(folder?.host_path || "").trim();
  const hasCanonicalContainerPath = /^\/data\/import\/pictures-\d+$/.test(rawFolder);

  let containerPath = rawFolder;
  let hostPath = storedHost;

  if (!hasCanonicalContainerPath) {
    if (!hostPath) {
      hostPath = rawFolder;
    }
    const index = padFolderIndex(folder?.id) || fallbackIndex;
    if (index) {
      containerPath = `/data/import/pictures-${index}`;
    }
  }

  if (!hostPath) {
    hostPath = containerPath;
  }

  return { hostPath, containerPath };
}

export function inferReferenceMount(folder, fallbackIndex = null) {
  const rawFolder = normalizeFolderPath(folder?.folder);
  if (!rawFolder) return null;

  const storedHost = String(folder?.host_path || "").trim();
  const hasCanonicalContainerPath = /^\/data\/ref\/pictures-\d+$/.test(rawFolder);

  let containerPath = rawFolder;
  let hostPath = storedHost;

  if (!hasCanonicalContainerPath) {
    if (!hostPath) {
      hostPath = rawFolder;
    }
    const index = padFolderIndex(folder?.id) || fallbackIndex;
    if (index) {
      containerPath = `/data/ref/pictures-${index}`;
    }
  }

  if (!hostPath) {
    hostPath = containerPath;
  }

  return { hostPath, containerPath };
}

/**
 * Build the full docker run restart command.
 *
 * @param {string[]} mounts - Volume mount flags, e.g. ["-v '/host:/container'", ...]
 * @param {"linux"|"windows"} format
 * @param {{ containerName: string, imageReference: string, isGpu: boolean }} options
 * @returns {string}
 */
export function buildDockerRestartCommand(mounts, format, { containerName, imageReference, isGpu }) {
  if (format === "windows") {
    const c = "`";
    const lines = [`docker rm -f ${containerName} 2>$null`, `docker run -d ${c}`];
    if (isGpu) {
      lines.push(`  --runtime nvidia ${c}`);
    }
    lines.push(`  -e HOME=/home/pixlstash ${c}`);
    if (isGpu) {
      lines.push(`  -e NVIDIA_VISIBLE_DEVICES=all ${c}`);
      lines.push(`  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility ${c}`);
    }
    lines.push(`  -e PIXLSTASH_HOST=0.0.0.0 ${c}`);
    lines.push(`  -p 9537:9537 ${c}`);
    lines.push(`  -v "$env:USERPROFILE\\Pictures\\pixlstash:/home/pixlstash" ${c}`);
    for (const mount of mounts) {
      lines.push(`  ${mount} ${c}`);
    }
    lines.push(`  --name ${containerName} ${c}`);
    lines.push(`  ${imageReference}`);
    return lines.join("\n");
  }

  // bash (Linux / macOS)
  const lines = [
    `docker rm -f ${containerName} 2>/dev/null || true`,
    "docker run -d \\",
  ];
  if (isGpu) {
    lines.push("  --runtime nvidia \\");
  }
  lines.push("  --user $(id -u):$(id -g) \\");
  lines.push("  -e HOME=/home/pixlstash \\");
  if (isGpu) {
    lines.push("  -e NVIDIA_VISIBLE_DEVICES=all \\");
    lines.push("  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \\");
  }
  lines.push("  -e PIXLSTASH_HOST=0.0.0.0 \\");
  lines.push("  -p 9537:9537 \\");
  lines.push("  -v ~/Pictures/pixlstash:/home/pixlstash \\");
  for (const mount of mounts) {
    lines.push(`  ${mount} \\`);
  }
  lines.push(`  --name ${containerName} \\`);
  lines.push(`  ${imageReference}`);
  return lines.join("\n");
}
