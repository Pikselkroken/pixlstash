# Windows code signing (Certum SimplySign)

How PixlStash's Windows release exes get Authenticode-signed in CI, and the
one-time setup needed to enable it.

## How it works

Certum's "Code Signing in the Cloud" (SimplySign) keeps the private key in
Certum's HSM. There is **no official headless signing API**: the key is only
reachable through the SimplySign Desktop GUI application, which exposes it as
a PKCS#11 token after an interactive login (account e-mail + a 6-digit TOTP
from the SimplySign mobile app).

CI automates exactly that, using the pattern proven by ReactiveUI's release
pipeline (github.com/reactiveui/actions-common):

```
build job (windows-latest)          sign job (ubuntu-latest, container)
┌─────────────────────────┐         ┌──────────────────────────────────────┐
│ build installer .exe    │ artifact│ certum-signer image:                 │
│ upload as artifact      ├────────▶│  Xvfb + SimplySign Desktop (GUI,     │
└─────────────────────────┘         │  driven by xdotool; TOTP computed    │
                                    │  from the seed) → PKCS#11 token      │
                                    │  jsign signs (SHA-256 + RFC 3161     │
                                    │  timestamp) → osslsigncode verifies  │
                                    │  → attach to GitHub release          │
                                    └──────────────────────────────────────┘
```

- Only Certum's own application touches key material. The automation
  (`.github/actions/certum-sign/action.yml`) is readable shell plus a
  ~15-line stdlib-Python TOTP implementation.
- The signer container (`.github/docker/certum-signer/Dockerfile`, published
  as `ghcr.io/pikselkroken/certum-signer`) bakes in SimplySign Desktop, jsign
  and the X11/PKCS#11 stack, all sha256-pinned. It contains **no secrets**.
- Signing runs only on `v*` tag pushes (plus the manual smoke test) because
  Certum caps signing at **5000 signatures/month**. A normal release spends 2
  (desktop installer + server installer).
- Signed artifacts: the outer installers only. The exes *inside* the desktop
  NSIS installer (PixlStash.exe, the uninstaller, the bundled Python) are not
  signed — SmartScreen evaluates the downloaded file, which is the signed
  installer. Signing the inner tree is a possible follow-up.

**Failure policy:** while the `WINDOWS_SIGNING_ENABLED` repo variable is set,
any signing failure is a hard red job and the release lacks its Windows exe
until the job is rerun. There is deliberately no "tried to sign, shipped
unsigned anyway" path. When the variable is *unset*, releases still ship, but
the Windows exes get an explicit `-unsigned` suffix and a workflow warning
(same convention as unsigned macOS builds).

## One-time Certum setup

1. Obtain the code signing certificate on a SimplySign (cloud) carrier and
   activate the SimplySign mobile app.
2. **Capture the TOTP seed during mobile activation.** The activation QR code
   is a standard `otpauth://totp/...?secret=...` URI, and this is the only
   moment it is visible — re-provisioning later resets the seed and the
   phone enrollment:
   - Screenshot the QR page.
   - Decode it **locally** (`sudo apt install zbar-tools && zbarimg shot.png`).
     Never use an online QR decoder — the seed is the complete second factor
     for your signing identity.
   - Store the full `otpauth://` URI in a password manager.
   - Verify: a TOTP generator fed this seed (password manager entry, or
     `oathtool --totp -b <secret>`) must show the **same 6-digit code as the
     SimplySign mobile app** at the same moment. If it does not, the captured
     QR was stale.
   - Delete the screenshot.
3. After the certificate is issued, note the **SHA-256 fingerprint of the
   leaf certificate** (Certum panel, or `openssl x509 -noout -fingerprint
   -sha256` on the exported public cert). Optional but recommended: the sign
   step asserts the signature was made by exactly this certificate.

## GitHub setup

1. Repo → Settings → Environments → **New environment: `windows-signing`**.
   Two settings are **mandatory, not optional** — they are the only controls
   preventing anyone with write access from exfiltrating the TOTP seed by
   dispatching a modified workflow against the environment:
   - **Required reviewer** (yourself): every signing job waits for explicit
     approval. Releases are rare; the friction is negligible.
   - **Deployment branches/tags restricted** to `v*` tags and `main`.
   Do not set `WINDOWS_SIGNING_ENABLED` until both are configured.
2. Add **environment secrets** on `windows-signing`:
   | Secret | Value |
   |---|---|
   | `CERTUM_USER_ID` | SimplySign login (the account e-mail) |
   | `CERTUM_OTP_URI` | the captured `otpauth://` URI |
   | `CERTUM_CERT_FINGERPRINT` | leaf cert SHA-256 fingerprint (optional) |
3. Make the `certum-signer` ghcr package pullable by Actions (public is
   simplest; the jobs also send `GITHUB_TOKEN` credentials, which covers a
   private package with repo access).
4. Set the **repository variable** `WINDOWS_SIGNING_ENABLED` = `true`
   (Settings → Secrets and variables → Actions → Variables). This is the
   master switch; unsetting it reverts releases to `-unsigned` artifacts
   without touching the secrets.

## Rollout / first validation

1. Merge this setup; run the **Build Certum Signer Image** workflow once. Its
   step summary prints the image digest.
2. Replace the placeholder digest (`@sha256:000...` with the
   `PLACEHOLDER-set-after-first-image-build` comment) in all three consumers:
   `electron.yml`, `windows-installer.yml`, `windows-signing-test.yml`.
3. Run the **Windows Signing Smoke Test** workflow. Green means: login,
   PKCS#11 token, signing, timestamp and verification all work. Download the
   artifact and check Properties → Digital Signatures on a Windows machine.
4. Rehearse with a prerelease tag (`vX.Y.Z-rc1`) before the next real
   release: both Windows exes should attach to the (pre)release with clean
   names, and `release-version.yml` should still fire.

## Operations

- **Every release tag now needs two manual approvals.** Pushing a `v*` tag is
  no longer fire-and-forget: **Build Electron Desktop App** and **Build
  Windows Installer** each pause at their sign job until the `windows-signing`
  environment's required reviewer approves the deployment (Actions → the run →
  "Review deployments"). Nothing signed attaches until both are approved; the
  builds themselves run first, so the prompts appear once each build finishes.
- **One SimplySign session at a time.** Certum allows a single active session
  per account, so `sign-windows-desktop` and `sign-windows-server` share the
  `certum-simplysign-session` concurrency group (`cancel-in-progress: false`)
  and run one after the other. A tag push starts both workflows in parallel;
  without the shared group the second login displaces the first and its
  PKCS#11 token never activates ("token not active after 3 login attempts").
  Keep the group name identical in both workflows, and give any future signing
  job the same group.
- **Bumping the signer image** (new SimplySign or jsign version): edit the
  Dockerfile args + sha256 pins (Certum publishes no checksums, so re-verify
  the new SimplySign hash from a second network vantage before merging) →
  merge → the image workflow prints a new digest → update the digest in the
  three consuming workflows → **run the smoke test before the next release
  tag**. The login automation clicks at
  fixed window-height fractions validated per SimplySign version, so an
  unvalidated bump can break login (loudly, never silently).
- **Budget:** 5000 signatures/month. Releases spend 2; smoke tests 1. Don't
  wire signing into non-release builds.
- **Rotating the seed:** re-provision SimplySign mobile access at Certum,
  capture the new QR the same way, update `CERTUM_OTP_URI`.
- **Disabling signing:** unset `WINDOWS_SIGNING_ENABLED`. Releases ship
  `-unsigned` exes again; nothing else changes.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "login window did not appear" | SimplySign needs longer to start: raise the action's `launch-seconds`. Re-run the smoke test with `debug-screenshots: true` and check the diagnostics artifact (screenshots may show the account e-mail; they should not show the OTP, assuming SimplySign masks the field — treat them as sensitive regardless). |
| Login submitted but "token not active" | The "Logon successful" dialog's Close button wasn't hit (the token stays inactive until it is closed) — usually SimplySign UI drift after an image bump. Re-validate coordinates via the smoke test. |
| OTP rejected | Seed mismatch (compare generator vs phone app) or container clock skew. |
| Segfault at launch, empty log | `$USER` unset — baked into the image (`ENV USER=root`); if it recurs, something cleared the environment. |
| jsign timestamp errors | `time.certum.pl` hiccup; the action already retries 3×. Persistent failures: try re-running the job. |
| Signing job waits forever | The `windows-signing` environment's required reviewer hasn't approved the deployment. |

## Related

- `.github/actions/certum-sign/action.yml` — the signing action (the "why"
  comments there are the authoritative description of the login choreography).
- `.github/docker/certum-signer/Dockerfile` — the signer image.
- `.github/workflows/windows-signing-test.yml` — the smoke test.
- macOS signing is configured separately (electron.yml; Apple Developer ID
  secrets).
