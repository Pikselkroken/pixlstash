# PixlStash Release Test Plan

This document defines the checks to perform before each release.
API/integration tests are automated and run via pytest — this plan covers only
what requires a browser, visual inspection, or a live external service.

---

## How to Use

1. Run the automated test suite first (see §1). All tests must pass.
2. Work through each manual item below.
3. Mark each item ✅ Pass / ❌ Fail / ⏭ Skip (with reason).
4. A release is only signed off when all non-skipped items pass.

---

## 0. Installation & First Launch

Perform all checks on a **clean machine** (no prior PixlStash install, no existing `vault.db`).  
Mark each cell ✅ Pass / ❌ Fail / ⏭ Skip (with reason).

---

### 0.1 Windows — Installer

| Check | Windows |
|-------|---------|
| Download the `.exe` installer from the latest GitHub release | |
| Run the installer as a normal user — completes without errors | |
| SmartScreen "More info → Run anyway" flow works as expected | |
| **PixlStash Server** Start Menu shortcut launches the server | |
| Browser opens to `http://localhost:9537` and the login page loads | |
| Log in with default credentials — grid page renders | |
| Import one image — it appears in the grid | |
| Close the console window — no stale processes remain (`tasklist` check) | |

---

### 0.2 pip + venv

Requires Python 3.10+. Test on all three platforms.

```
python -m venv venv
# Linux/macOS:  source venv/bin/activate
# Windows:      venv\Scripts\activate
pip install pixlstash
pixlstash-server
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `pip install pixlstash` completes without errors | | | |
| `pixlstash-server` starts; AI models download on first run | | | |
| `http://localhost:9537` loads the login page | | | |
| Log in and import one image — it appears in the grid | | | |
| Ctrl-C shuts down cleanly (no lock files or corrupt DB) | | | |

---

### 0.3 Docker — CPU image

Works on Linux, macOS, and Windows (Docker Desktop / Docker Engine required).

```bash
docker run --rm -e PIXLSTASH_HOST=0.0.0.0 -p 9537:9537 \
  ghcr.io/pikselkroken/pixlstash:latest
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| Container starts without errors | | | |
| `http://localhost:9537` loads the login page | | | |
| Log in and import one image — it appears in the grid | | | |
| Ctrl-C stops the container cleanly | | | |

---

### 0.4 Docker — GPU image (Ubuntu 24.04 / WSL2)

Requires NVIDIA Container Toolkit installed and Docker restarted.  
Verify GPU access first: `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`

```bash
mkdir -p ~/Pictures/pixlstash
docker run -d \
  --runtime nvidia \
  --user $(id -u):$(id -g) \
  -e HOME=/home/pixlstash \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -e PIXLSTASH_HOST=0.0.0.0 \
  -p 9537:9537 \
  -v ~/Pictures/pixlstash:/home/pixlstash \
  --name pixlstash \
  ghcr.io/pikselkroken/pixlstash:latest-gpu
```

| Check | Ubuntu 24.04 | WSL2 (Windows) |
|-------|--------------|----------------|
| `nvidia-smi` inside the CUDA verification container shows the GPU | | |
| GPU container starts without errors | | |
| Server log confirms CUDA / GPU inference (no CPU-fallback warning) | | |
| `http://localhost:9537` loads the login page | | |
| Import one image and confirm background tagging completes | | |

---

### 0.5 From Source

Requires Python 3.10+, Node.js 20+, npm.

```bash
git clone https://github.com/pikselkroken/pixlstash.git && cd pixlstash
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install --upgrade pip && pip install -e .
cd frontend && npm ci && npm run build && cd ..
pixlstash-server
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `pip install -e .` completes without errors | | | |
| `npm run build` completes without errors | | | |
| `pixlstash-server` starts and `http://localhost:9537` loads | | | |
| Import one image — it appears in the grid | | | |

---

## 1. Automated Tests

```bash
python -m pytest -s -vvv --fast-captions --force-cpu
ruff check pixlstash
```

- [ ] All pytest tests pass with no errors
- [ ] Ruff reports zero lint violations

---

## 2. Authentication

### 2.1 Login / Logout
- [ ] Logout in the browser redirects to the login screen
- [ ] Accessing a protected route while logged out redirects to the login screen

### 2.2 API Tokens
- [ ] Generated token appears in the UI token list

### 2.3 Session Persistence
- [ ] Reload the page while logged in — session is preserved and grid loads

---

## 3. Configuration

- [ ] "Open server config" action opens the config location in the OS file browser

---

## 4. Picture Import

- [ ] Open the import dialog
- [ ] Drag-and-drop image files into the drop zone — files appear in the preview list
- [ ] Complete the import — pictures appear in the grid

---

## 5. Image Grid & Browsing

### 5.1 Grid Display
- [ ] Images render correctly with no broken thumbnails in the browser
- [ ] Infinite scroll loads additional pages of results
- [ ] Changing column count updates the layout immediately

### 5.2 Sorting & Filtering
- [ ] Sort dropdown in the UI lists all available sort values
- [ ] Changing sort order re-renders the grid in the expected order

### 5.3 Search
- [ ] Clearing the search field resets the grid to the unfiltered state
- [ ] Search history is visible during the session (in-memory, not persisted to localStorage)

### 5.4 Stacks View
- [ ] Clicking a stack in the UI shows member pictures in order

---

## 6. Picture Detail (Lightbox / ImageOverlay)

- [ ] Clicking a picture opens the lightbox and displays the metadata
- [ ] Navigating to next/previous picture works (keyboard arrows + buttons)
- [ ] Closing the lightbox returns to the grid without a page reload

---

## 7. Tags

- [ ] Tag add/remove is reflected immediately in the UI without a page reload

---

## 8. Star Rating / Quality Score

- [ ] Open the star rating overlay on a picture
- [ ] Rating change persists after closing and reopening the lightbox
- [ ] Quality metrics (blur, noise, contrast) are displayed in the detail view

---

## 9. Picture Sets

- [ ] Set appears in the sidebar with correct picture count
- [ ] Removing pictures from the set updates the sidebar count

---

## 10. Projects

- [ ] Project appears in the sidebar with a matching picture count

---

## 11. Characters

- [ ] Character appears in the sidebar with thumbnail and correct face count

---

## 12. Faces

- [ ] Faces appear with correct bounding box overlays in the picture detail view

---

## 13. Stacks

- [ ] Stacks are displayed correctly in the grid and stack browser

---

## 14. ComfyUI Integration

- [ ] Run a text-to-image workflow — output appears as a new picture
- [ ] Run an image-to-image workflow — output appears, linked to the source
- [ ] Live progress is visible during execution (WebSocket updates)
- [ ] Abort a running workflow — execution stops cleanly

---

## 15. Image Plugins

- [ ] **Blur/Sharpen**: apply to a picture — result is visibly blurred or sharpened
- [ ] **Brightness/Contrast**: apply to a picture — brightness/contrast is visibly changed
- [ ] **Colour Filter**: apply to a picture — colour shift is clearly visible

---

## 16. Tag Predictions

- [ ] Tag predictions UI shows pending predictions for a freshly tagged picture
- [ ] Accepting a prediction adds it as a confirmed tag and removes it from the predictions list
- [ ] Rejecting a prediction removes it from the list without adding it as a tag

---

## 17. Picture Sharing

> **Note:** Picture sharing (share links / public access) is not yet implemented as of this version.
> When the feature is added, add API tests and expand this section with manual browser checks.

---

## 18. Export

- [ ] Download a picture export via the UI — ZIP opens correctly and contains expected files
- [ ] Download a project export via the UI — ZIP contains the project JSON and expected assets

---

## 19. Background Task System

- [ ] Worker progress overlay displays live task status in the UI
- [ ] After import and background processing, quality metrics are visible on pictures in the grid
- [ ] After processing, face crops are visible in the picture detail view
- [ ] After processing, captions/descriptions are visible on pictures

---

## 20. Performance & Stability

- [ ] Scroll through a large grid (≥200 pictures) — no visible frame drops or missing thumbnails
- [ ] UI remains responsive while a long background task (face extraction on 20+ images) runs

---

## Changelog Reference

Update this section after each release noting any test items added, removed, or changed.

| Release | Notes |
|---------|-------|
| (initial) | Plan created; covers all features as of April 2026 |
| v-next | Automated all `[A-py]` items into pytest; this plan now covers manual-only checks |
| v-next | Added §0 cross-platform installation checks (macOS, Ubuntu 24.04, Windows) |
