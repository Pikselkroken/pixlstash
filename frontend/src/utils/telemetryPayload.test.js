import { afterEach, describe, it, expect } from "vitest";
import {
  buildInstallPingRequest,
  buildPayloadForChoice,
  buildPayloadLegend,
  buildVersionCheckRequest,
  defaultInstallType,
} from "./telemetryPayload";

const CONTEXT = {
  version: "1.9.0",
  installType: "pip",
  installId: "9f2c1b7e-4d5a-4c81-b3e6-8a7d2f0e5c14",
  isNewInstall: true,
};

describe("buildPayloadForChoice", () => {
  it("sends nothing at all for the decline option", () => {
    expect(buildPayloadForChoice("none", CONTEXT)).toEqual([]);
  });

  it("sends only the version check when the ID is off", () => {
    const requests = buildPayloadForChoice("check", CONTEXT);
    expect(requests).toHaveLength(1);
    expect(requests[0].method).toBe("GET");
    expect(requests[0].body).toBeUndefined();
  });

  it("adds the ping when the ID is on", () => {
    const requests = buildPayloadForChoice("checkid", CONTEXT);
    expect(requests).toHaveLength(2);
    expect(requests[1].method).toBe("POST");
  });

  it("shows only the ping when an existing update-check opt-out adds the ID", () => {
    const requests = buildPayloadForChoice("id", CONTEXT);
    expect(requests).toHaveLength(1);
    expect(requests[0].method).toBe("POST");
    expect(requests[0].url).toContain("t.pixlstash.dev");
  });

  it("puts the version and install type in the version-check path", () => {
    const { url } = buildVersionCheckRequest("1.9.0", "docker");
    expect(url).toBe("https://pixlstash.dev/latest-version/1.9.0/docker.json");
  });

  it("sends the ping to a host separate from the website", () => {
    // The version check must stay a pure static origin with no compute in
    // front of it, so the ping cannot share its route.
    const [check, ping] = buildPayloadForChoice("checkid", CONTEXT);
    expect(new URL(check.url).host).not.toBe(new URL(ping.url).host);
  });
});

describe("buildInstallPingRequest", () => {
  it("sends exactly the three keys the Worker accepts", () => {
    const { body } = buildInstallPingRequest({
      installId: CONTEXT.installId,
      isNewInstall: false,
      installType: "electron",
    });
    // The Worker rejects any unrecognised key outright, so an extra field here
    // would make every ping a 400.
    expect(Object.keys(body).sort()).toEqual([
      "install_id",
      "install_type",
      "is_new_install",
    ]);
  });

  it("carries no version, timestamp, or anything about the machine", () => {
    const { body } = buildInstallPingRequest({
      installId: CONTEXT.installId,
      isNewInstall: true,
      installType: "pip",
    });
    const serialised = JSON.stringify(body);
    expect(serialised).not.toMatch(/1\.9\.0/);
    expect(serialised).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });
});

describe("buildPayloadLegend", () => {
  it("names every varying value in the payload", () => {
    const legend = buildPayloadLegend("checkid", CONTEXT);
    const terms = legend.map((entry) => entry.term);
    expect(terms).toContain("1.9.0");
    expect(terms).toContain("pip");
    expect(terms).toContain("install_id");
    expect(terms).toContain("is_new_install");
  });

  it("explains that pip covers the Windows server installer", () => {
    // Only the Electron shell sets PIXLSTASH_INSTALL_TYPE; everything else
    // falls through to the pip default, so a Windows user sees "pip" and would
    // otherwise reasonably think we had them wrong.
    const legend = buildPayloadLegend("check", CONTEXT);
    const installTypeEntry = legend.find((entry) => entry.term === "pip");
    expect(installTypeEntry.meaning).toMatch(/Windows server installer/);
  });

  it("explains the same install-type value for the ID option too", () => {
    const legend = buildPayloadLegend("checkid", CONTEXT);
    const installTypeEntry = legend.find((entry) => entry.term === "pip");
    expect(installTypeEntry.meaning).toMatch(/Windows server installer/);
  });

  it("names nothing when nothing is sent", () => {
    expect(buildPayloadLegend("none", CONTEXT)).toEqual([]);
  });

  it("does not claim a version is sent for the ID-only choice", () => {
    const legend = buildPayloadLegend("id", CONTEXT);
    expect(legend.map((entry) => entry.term)).not.toContain("1.9.0");
    expect(legend.map((entry) => entry.term)).toContain("install_id");
  });

  it("reflects the running install rather than a hardcoded sample", () => {
    const legend = buildPayloadLegend("check", {
      version: "2.0.1",
      installType: "docker",
    });
    expect(legend.map((entry) => entry.term)).toEqual(["2.0.1", "docker"]);
  });
});

describe("defaultInstallType", () => {
  const META = 'meta[name="pixlstash-install-type"]';
  const setMeta = (content) => {
    document.querySelector(META)?.remove();
    const el = document.createElement("meta");
    el.setAttribute("name", "pixlstash-install-type");
    el.setAttribute("content", content);
    document.head.appendChild(el);
  };

  afterEach(() => document.querySelector(META)?.remove());

  // Regression guard. This value is what the version check reports, because it
  // fires from TitleBar's onMounted before App.vue's GET /version resolves and
  // stamps its 24h throttle before the request. Assuming "pip" emptied the
  // `electron` bucket when 1.10.1 shipped and every desktop user restarted, and
  // had been undercounting docker the same way all along.
  it.each(["docker", "electron", "other", "dev"])(
    "reports the %s the server substituted in",
    (installType) => {
      setMeta(installType);
      expect(defaultInstallType()).toBe(installType);
    },
  );

  it("ignores the placeholder the dev server leaves behind", () => {
    setMeta("__PIXLSTASH_INSTALL_TYPE__");
    expect(defaultInstallType()).toBe("pip");
  });

  it("falls back to pip when the document carries no marker at all", () => {
    expect(defaultInstallType()).toBe("pip");
  });
});
