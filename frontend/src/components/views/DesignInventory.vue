<template>
  <div class="di">
    <header class="di-head">
      <div>
        <div class="section-label">PixlStash · current controls</div>
        <h1 class="di-title">Every control the app ships, as it ships</h1>
        <p class="di-lede">
          Rendered by the real components under the real theme, so what is on
          this page is what is on the screen. Dev-only route
          (<code>/design</code>). Counts are tag occurrences in
          <code>frontend/src</code> on 2026-09-10.
        </p>
      </div>
      <div class="di-head__tools">
        <AppButton
          variant="secondary"
          size="sm"
          :icon-left="isDark ? 'weather-sunny' : 'weather-night'"
          @click="toggleTheme"
        >
          {{ isDark ? "Light theme" : "Dark theme" }}
        </AppButton>
      </div>
    </header>

    <nav class="di-nav" aria-label="Sections">
      <a v-for="s in sections" :key="s.id" :href="`#${s.id}`">{{
        s.label
      }}</a>
    </nav>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="tokens" class="di-section">
      <h2 class="di-h2">Tokens</h2>
      <p class="di-note">
        Color from <code>main.js</code> (Vuetify themes); everything else from
        <code>styles/design-tokens.css</code>.
      </p>

      <h3 class="di-h3">Color, grouped by job</h3>
      <div v-for="g in colorGroups" :key="g.name" class="di-swatch-group">
        <div class="section-label">{{ g.name }}</div>
        <div class="di-swatches">
          <div v-for="k in g.keys" :key="k" class="di-swatch">
            <div
              class="di-swatch__chip"
              :style="{
                background: `rgb(var(--v-theme-${k}))`,
                color: colors[`on-${k}`]
                  ? `rgb(var(--v-theme-on-${k}))`
                  : undefined,
              }"
            >
              <span v-if="colors[`on-${k}`]">on-{{ k }}</span>
            </div>
            <div class="di-swatch__name">{{ k }}</div>
            <div class="di-swatch__hex">{{ colors[k] }}</div>
          </div>
        </div>
      </div>

      <h3 class="di-h3">Type ramp</h3>
      <div class="di-type">
        <div
          v-for="t in typeRamp"
          :key="t.token"
          class="di-type__row"
          :style="{ fontSize: `var(--text-${t.token})` }"
        >
          <span class="di-type__meta">--text-{{ t.token }} · {{ t.px }}</span>
          <span>{{ t.use }}</span>
        </div>
      </div>

      <div class="di-grid-3">
        <div>
          <h3 class="di-h3">Spacing (4px grid)</h3>
          <div class="di-spacing">
            <div v-for="n in 9" :key="n" class="di-spacing__row">
              <span class="di-type__meta">--space-{{ n }}</span>
              <span
                class="di-spacing__bar"
                :style="{ width: `var(--space-${n})` }"
              ></span>
            </div>
          </div>
        </div>
        <div>
          <h3 class="di-h3">Radius</h3>
          <div class="di-radii">
            <div
              v-for="r in ['sm', 'md', 'lg', 'pill']"
              :key="r"
              class="di-radius"
              :style="{ borderRadius: `var(--radius-${r})` }"
            >
              {{ r }}
            </div>
          </div>
        </div>
        <div>
          <h3 class="di-h3">Elevation</h3>
          <div class="di-elev">
            <div
              v-for="n in 4"
              :key="n"
              class="di-elev__card"
              :style="{ boxShadow: `var(--elevation-${n})` }"
            >
              {{ n }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="buttons" class="di-section">
      <h2 class="di-h2">Buttons</h2>
      <p class="di-note">
        Seven dialects. <code>AppButton</code> 117 tags in 34 files ·
        <code>v-btn</code> 65 in 12 · <code>.tbm-action</code> 16 in 8 ·
        <code>.bar-btn</code> 42 in 6 · <code>.selbar-btn</code> 10 in 2 ·
        <code>.ctx-item</code> 98 in 4 · plus 508 bare
        <code>&lt;button&gt;</code> tags in 81 files that paint themselves.
      </p>

      <Spec
        name="AppButton"
        where="widgets/AppButton.vue · the design-system primitive"
      >
        <div class="di-row">
          <AppButton
            v-for="v in appBtnVariants"
            :key="v"
            :variant="v"
            icon-left="check"
            >{{ v }}</AppButton
          >
        </div>
        <div class="di-row">
          <AppButton
            v-for="v in appBtnVariants"
            :key="v"
            :variant="v"
            size="sm"
            >{{ v }} sm</AppButton
          >
        </div>
        <div class="di-row">
          <AppButton variant="primary" disabled>Disabled</AppButton>
          <AppButton variant="primary" loading>Loading</AppButton>
          <AppButton variant="secondary" key-hint="esc">Cancel</AppButton>
          <AppButton variant="primary_green" key-hint="enter">Save</AppButton>
          <AppButton variant="danger" key-hint="s">Keep separate</AppButton>
          <AppButton variant="ghost" icon-only icon-left="dots-horizontal" />
          <AppButton
            variant="secondary"
            icon-only
            size="sm"
            icon-left="close"
          />
          <HelpTip label="Why" reason="A help tip is a ghost icon button." />
        </div>
      </Spec>

      <Spec
        name="v-btn, in the prop combinations the app actually uses"
        where="FolderEditor, ImageGrid, SideBar, RestoreConfirmDialog, ShareDialog, PrivacySection, FolderBrowser…"
      >
        <div class="di-row">
          <v-btn size="small" variant="text">text small</v-btn>
          <v-btn variant="outlined" color="primary">outlined primary</v-btn>
          <v-btn variant="flat" color="primary">flat primary</v-btn>
          <v-btn variant="tonal">tonal</v-btn>
          <v-btn variant="elevated">elevated</v-btn>
          <v-btn variant="outlined" color="error">outlined error</v-btn>
          <v-btn color="error" size="small">error small</v-btn>
        </div>
        <div class="di-row">
          <v-btn size="small" variant="text" prepend-icon="mdi-plus"
            >prepend-icon</v-btn
          >
          <v-btn variant="outlined" rounded="lg">rounded lg</v-btn>
          <v-btn density="compact" variant="text">density compact</v-btn>
          <v-btn color="primary" loading>loading</v-btn>
          <v-btn disabled>disabled</v-btn>
          <v-btn icon="mdi-delete" size="x-small" />
          <v-btn icon="mdi-folder-plus-outline" size="36px" variant="text" />
          <v-btn icon="mdi-help-circle-outline" size="28px" variant="text" />
          <v-btn-toggle v-model="shellFormat" mandatory density="compact">
            <v-btn value="linux" size="small">Linux / Mac</v-btn>
            <v-btn value="windows" size="small">Windows</v-btn>
          </v-btn-toggle>
        </div>
      </Spec>

      <Spec
        name="Toolbar bar buttons"
        where="App.css .bar-btn · Toolbar, ModelShelf, SelectionBar, TbOverflowMenu"
      >
        <div class="di-toolbar">
          <button type="button" class="bar-btn bar-btn--boxed">
            <v-icon size="18">mdi-sort</v-icon>
            <span class="bar-btn-prefix">Sort:</span>
            <span class="bar-btn-label">Date added</span>
            <v-icon size="16">mdi-menu-down</v-icon>
          </button>
          <button type="button" class="bar-btn bar-btn--boxed bar-btn--active">
            <span class="bar-icon-badge-wrap">
              <v-icon size="18">mdi-filter-variant</v-icon>
              <span class="bar-filter-badge">3</span>
            </span>
            <span class="bar-btn-label">Filter</span>
          </button>
          <span class="di-toolbar__split">
            <button type="button" class="bar-btn bar-split-toggle">
              <v-icon size="18">mdi-view-grid</v-icon>
              <span class="bar-btn-label">Grid</span>
            </button>
            <button type="button" class="bar-btn bar-split-menu">
              <v-icon size="16">mdi-menu-down</v-icon>
            </button>
          </span>
          <button type="button" class="bar-btn bar-btn--icon" title="Stats">
            <v-icon size="20">mdi-chart-bar</v-icon>
          </button>
          <TbOverflowMenu>
            <template #default="{ close }">
              <button type="button" class="ctx-item" @click="close">
                <v-icon size="16" class="ctx-icon">mdi-download</v-icon>
                Export…
              </button>
              <button type="button" class="ctx-item" @click="close">
                <v-icon size="16" class="ctx-icon">mdi-share-variant</v-icon>
                Share…
              </button>
            </template>
          </TbOverflowMenu>
        </div>
      </Spec>

      <Spec
        name="Selection pill verbs"
        where="App.css .selbar · SelectionBar (in GridActionPill)"
      >
        <div class="di-row">
          <div class="selbar">
            <button type="button" class="selbar-count">
              <v-icon size="16">mdi-checkbox-marked-outline</v-icon>
              12 selected <span class="selbar-size">· 48 MB</span>
              <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
            <span class="selbar-sep"></span>
            <button type="button" class="selbar-btn" title="Add to person">
              <v-icon size="18">mdi-account-plus-outline</v-icon>
            </button>
            <button type="button" class="selbar-btn" title="Add to set">
              <v-icon size="18">mdi-folder-plus-outline</v-icon>
            </button>
            <button type="button" class="selbar-btn" title="Rotate" disabled>
              <v-icon size="18">mdi-rotate-right</v-icon>
            </button>
            <span class="selbar-sep"></span>
            <button
              type="button"
              class="selbar-btn selbar-btn--danger"
              title="Move to Scrapheap"
            >
              <v-icon size="18">mdi-delete-outline</v-icon>
            </button>
          </div>
        </div>
      </Spec>

      <Spec
        name="Popover menu actions"
        where="App.css .tbm-action / .tbm-btn / .tbm-seg / .tbm-toggle · Toolbar, GbFilterPanel, ShelfSortPanel…"
      >
        <div class="di-row di-row--panel">
          <button type="button" class="tbm-action tbm-action--primary">
            Apply
          </button>
          <button type="button" class="tbm-action tbm-action--secondary">
            Clear
          </button>
          <button type="button" class="tbm-action tbm-action--outline">
            Reset
          </button>
          <button
            type="button"
            class="tbm-action tbm-action--primary tbm-action--lg"
          >
            Large
          </button>
          <button type="button" class="tbm-action tbm-action--primary" disabled>
            Disabled
          </button>
        </div>
        <div class="di-row di-row--panel">
          <div class="tbm-btngroup" style="width: 320px">
            <button type="button" class="tbm-btn tbm-btn--on">Any</button>
            <button type="button" class="tbm-btn">Has face</button>
            <button type="button" class="tbm-btn">No face</button>
          </div>
          <div class="tbm-seg">
            <button type="button" class="tbm-seg-btn tbm-seg-btn--on">
              Grid
            </button>
            <button type="button" class="tbm-seg-btn">List</button>
            <button type="button" class="tbm-seg-btn">Stacks</button>
          </div>
        </div>
        <div class="di-row di-row--panel">
          <div class="tbm-grid-2" style="width: 320px">
            <button type="button" class="tbm-toggle tbm-toggle--on">
              <v-icon size="16" class="tbm-toggle-icon"
                >mdi-sort-calendar-descending</v-icon
              >
              <span class="tbm-toggle-label">Date added</span>
              <span class="tbm-toggle-end"
                ><v-icon size="16">mdi-check</v-icon></span
              >
            </button>
            <button type="button" class="tbm-toggle">
              <v-icon size="16" class="tbm-toggle-icon">mdi-star-outline</v-icon>
              <span class="tbm-toggle-label">Rating</span>
            </button>
            <button type="button" class="tbm-toggle">
              <v-icon size="16" class="tbm-toggle-icon"
                >mdi-image-size-select-large</v-icon
              >
              <span class="tbm-toggle-label">Resolution</span>
            </button>
            <button type="button" class="tbm-toggle tbm-toggle--pending">
              <v-icon size="16" class="tbm-toggle-icon">mdi-loading mdi-spin</v-icon>
              <span class="tbm-toggle-label">Smart score</span>
            </button>
          </div>
        </div>
      </Spec>

      <Spec
        name="Context-menu items"
        where="styles/context-menu.css .ctx-item · ImageGridContextMenu, Toolbar, SelectionMenu"
      >
        <div class="di-row">
          <div class="di-menu">
            <div class="ctx-readonly-header">
              <span class="ctx-readonly-pill"
                ><v-icon size="11">mdi-lock-outline</v-icon> read-only</span
              >
            </div>
            <button type="button" class="ctx-item">
              <v-icon size="16" class="ctx-icon">mdi-open-in-new</v-icon>
              Open <span class="ctx-shortcut">Enter</span>
            </button>
            <button type="button" class="ctx-item">
              <v-icon size="16" class="ctx-icon">mdi-tag-multiple</v-icon>
              Tag automatically
              <span class="ctx-default-pill">default</span>
              <v-icon size="16" class="ctx-arrow">mdi-chevron-right</v-icon>
            </button>
            <button type="button" class="ctx-item" disabled>
              <v-icon size="16" class="ctx-icon">mdi-rotate-right</v-icon>
              Rotate right
            </button>
            <div class="ctx-sep"></div>
            <button type="button" class="ctx-item ctx-item--danger">
              <v-icon size="16" class="ctx-icon">mdi-delete-outline</v-icon>
              Move to Scrapheap <span class="ctx-shortcut">Del</span>
            </button>
          </div>
        </div>
      </Spec>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="inputs" class="di-section">
      <h2 class="di-h2">Inputs</h2>
      <p class="di-note">
        <code>AppInput</code> 17 · <code>v-text-field</code> 14 ·
        <code>.tbm-input</code> 10 · bare <code>&lt;input&gt;</code> 76 ·
        <code>AppSelect</code> 7 · <code>v-select</code> 7 · bare
        <code>&lt;select&gt;</code> 39.
      </p>

      <Spec name="AppInput / AppSelect / AppTextarea / AppStepper" where="widgets/App*.vue">
        <div class="di-form">
          <AppInput v-model="txt" label="Name" placeholder="A person's name" />
          <AppInput v-model="txt" label="Search" icon="magnify" placeholder="Search…" />
          <AppInput v-model="txt" label="Disabled" disabled />
          <AppSelect v-model="sel" label="Sort by" :options="selOptions" />
          <AppSelect v-model="sel" label="Compact" compact :options="selOptions" />
          <AppSelect
            v-model="multi"
            label="Multiple"
            multiple
            :options="selOptions"
          />
          <AppTextarea v-model="txt" label="Description" placeholder="Notes…" />
          <div>
            <FieldLabel>Stepper</FieldLabel>
            <AppStepper v-model="num" :min="0" :max="100" />
          </div>
        </div>
      </Spec>

      <Spec
        name="v-text-field / v-select, as used"
        where="FolderEditor (filled comfortable), ShareDialog, SideBar, TaggerPluginSettingsDialog (outlined compact)"
      >
        <div class="di-form">
          <v-text-field
            v-model="txt"
            label="Local folder (host path)"
            variant="filled"
            density="comfortable"
            hide-details
          />
          <v-text-field
            v-model="txt"
            label="Label"
            variant="filled"
            density="compact"
            hide-details
          />
          <v-text-field
            v-model="txt"
            label="Expires at"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-select
            v-model="sel"
            :items="selOptions"
            item-title="label"
            item-value="value"
            label="Tagger"
            variant="outlined"
            density="compact"
            hide-details
          />
        </div>
      </Spec>

      <Spec
        name="Popover inputs"
        where="App.css .tbm-input / .tbm-num · GbFilterPanel, Toolbar"
      >
        <div class="di-row di-row--panel">
          <div class="tbm-input-wrap" style="width: 240px">
            <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
            <input class="tbm-input tbm-input--with-icon" placeholder="Tag…" />
          </div>
          <input class="tbm-input" placeholder="Plain" style="width: 160px" />
          <input class="tbm-num" value="1024" />
        </div>
      </Spec>

      <Spec
        name="Native range and DedupThresholdControl"
        where="widgets/DedupThresholdControl.vue · SearchResultBar (4 type=range)"
      >
        <div class="di-row">
          <DedupThresholdControl :threshold="0.82" :min="0.5" :max="0.95" />
          <label class="di-range"
            >Thumbnail size
            <input type="range" min="0" max="10" value="4"
          /></label>
        </div>
      </Spec>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="toggles" class="di-section">
      <h2 class="di-h2">Toggles, choices, sliders</h2>
      <p class="di-note">
        <code>v-switch</code> 16 · <code>v-checkbox</code> 11 · native
        <code>type=checkbox</code> 26 · <code>role=radio</code> 8 ·
        <code>v-slider</code> 4 · native <code>type=range</code> 4 ·
        <code>v-btn-toggle</code> 2 · <code>v-tabs</code> 1.
      </p>

      <Spec name="v-switch / v-checkbox, as used" where="Settings sections, TbExportPanel, RestoreConfirmDialog">
        <div class="di-row">
          <v-switch
            v-model="on"
            label="Switch, color accent"
            color="accent"
            density="compact"
            hide-details
          />
          <v-switch
            v-model="on"
            label="Switch, color primary"
            color="primary"
            density="compact"
            hide-details
          />
          <v-checkbox
            v-model="on"
            label="Checkbox"
            density="compact"
            hide-details
          />
          <v-checkbox
            v-model="on"
            label="Checkbox error"
            color="error"
            density="compact"
            hide-details
          />
          <v-checkbox
            v-model="on"
            label="Checkbox warning"
            color="warning"
            density="compact"
            hide-details
          />
        </div>
      </Spec>

      <Spec name="Native checkboxes in popovers" where="App.css .tbm-check · GbFilterPanel, TbExportPanel">
        <div class="di-row di-row--panel">
          <div class="tbm-check-grid" style="width: 320px">
            <label class="tbm-check"><input type="checkbox" checked /> Images</label>
            <label class="tbm-check"><input type="checkbox" /> Videos</label>
            <label class="tbm-check"><input type="checkbox" checked /> Stacks</label>
            <label class="tbm-check"><input type="checkbox" disabled /> Faces</label>
          </div>
        </div>
      </Spec>

      <Spec name="v-tabs" where="io/PhotosImportDialog.vue (the only one)">
        <v-tabs v-model="tab">
          <v-tab value="local">Local</v-tab>
          <v-tab value="icloud">iCloud</v-tab>
          <v-tab value="google">Google Photos</v-tab>
          <v-tab value="flickr">Flickr</v-tab>
        </v-tabs>
      </Spec>

      <Spec name="Sliders" where="SettingsSliderRow (v-slider accent) · SideBar, StatsSidebar (v-slider primary)">
        <div class="di-form">
          <SettingsSliderRow v-model="num" :min="0" :max="100" suffix="%" />
          <v-slider
            v-model="num"
            :min="0"
            :max="100"
            color="primary"
            density="compact"
            hide-details
          />
        </div>
      </Spec>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="pills" class="di-section">
      <h2 class="di-h2">Chips, pills, badges, marks</h2>
      <p class="di-note">
        Every one of these is hand-rolled except <code>v-chip</code> (4 tags).
      </p>

      <Spec name="v-chip / SettingsChip / kbd / labels" where="SnapshotsSection (v-chip tonal x-small) · settings/SettingsChip.vue · App.css kbd · style.css .section-label · widgets/FieldLabel.vue">
        <div class="di-row">
          <v-chip variant="tonal" size="x-small">manual</v-chip>
          <v-chip variant="tonal" size="x-small" color="error">error</v-chip>
          <v-chip variant="tonal" size="small">small</v-chip>
          <SettingsChip label="landscape" meta="120" />
          <SettingsChip label="portrait" meta="+4" meta-color="rgb(var(--v-theme-success))" />
          <kbd>Ctrl</kbd> <kbd>Shift</kbd> <kbd>↵</kbd>
          <span class="section-label">Section label</span>
          <FieldLabel>Field label</FieldLabel>
        </div>
      </Spec>

      <Spec name="Duplicate-queue pills" where="widgets/DedupConfidencePill, DedupScopePill, DedupWhyPills">
        <div class="di-row">
          <DedupConfidencePill :group="{ tier: 'exact' }" />
          <DedupConfidencePill :group="{ tier: 'near', confidence: 0.94 }" />
          <DedupConfidencePill :group="{ tier: 'near' }" />
          <DedupScopePill label="Summer 2025" :count="1284" />
          <DedupScopePill label="Anna" icon="mdi-account" :count="null" />
        </div>
        <div class="di-row">
          <DedupWhyPills :why="whyPills" />
          <DedupWhyPills :why="whyPills" variant="fact" />
        </div>
      </Spec>

      <Spec name="Stack badges on a tile" where="widgets/StackBadge.vue · ImageGrid, DuplicateQueue, MixedQueueRow">
        <div class="di-row di-row--tiles">
          <div v-for="b in stackBadges" :key="b.label" class="di-tile">
            <StackBadge v-bind="b.props" />
            <span class="di-tile__cap">{{ b.label }}</span>
          </div>
          <div class="di-tile">
            <span class="di-tile__badge di-tile__badge--count">12</span>
            <span class="di-tile__cap">count pill (spec §12)</span>
          </div>
          <div class="di-tile">
            <span class="di-tile__badge di-tile__badge--dot"></span>
            <span class="di-tile__cap">attention dot</span>
          </div>
        </div>
      </Spec>

      <Spec name="Marks and stickers" where="widgets/ModelMark, StarRatingOverlay, TelemetryOptionMark · reviews/ReviewSticker">
        <div class="di-row">
          <ModelMark :row="modelRow" />
          <ModelMark :row="modelRow" :ring="ringNone" />
          <ModelMark :row="modelRow" :ring="ringThick" :style="{ '--mmark-ring': ringThick.hue }" />
          <ModelMark :row="modelRow" :ring="ringDashed" :style="{ '--mmark-ring': ringDashed.hue }" />
          <ModelMark :row="modelRow" :ring="ringDouble" :style="{ '--mmark-ring': ringDouble.hue }" />
          <span class="di-sep"></span>
          <ReviewSticker icon="mdi-check" color="#2a7d3e" label="Kept" />
          <ReviewSticker icon="mdi-close" color="#b54538" label="Dropped" :tilt="4" />
          <ReviewSticker icon="mdi-star" color="#c47a1e" label="Star" fresh />
          <span class="di-sep"></span>
          <TelemetryOptionMark v-for="v in ['none', 'check', 'id', 'checkid']" :key="v" :variant="v" class="di-tmark" />
        </div>
        <div class="di-row">
          <div class="di-tile di-tile--wide">
            <StarRatingOverlay :score="3" icon-size="small" />
          </div>
          <div class="di-tile di-tile--wide">
            <StarRatingOverlay :score="4" compact icon-size="small" />
          </div>
          <div class="di-tile di-tile--wide">
            <StarRatingOverlay :score="5" number-mode icon-size="small" />
          </div>
        </div>
      </Spec>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="feedback" class="di-section">
      <h2 class="di-h2">Feedback and status</h2>
      <p class="di-note">
        <code>v-progress-circular</code> 21 tags at seven sizes ·
        <code>NoticeHost</code> is the one notice surface · <code>v-snackbar</code>
        2 · <code>v-alert</code> 1 · <code>ProgressOverlay</code> 4.
      </p>

      <Spec name="Spinners, at every size the app uses" where="size 10 · 14 · 16 · 18 · 20 · 24 · 32; color primary (5) or accent (1)">
        <div class="di-row">
          <v-progress-circular
            v-for="s in [10, 14, 16, 18, 20, 24, 32]"
            :key="s"
            indeterminate
            :size="s"
            :width="s < 18 ? 2 : 3"
            color="primary"
          />
          <v-progress-circular indeterminate size="20" color="accent" />
          <v-icon class="mdi-spin" size="18">mdi-loading</v-icon>
          <span class="di-cap">← mdi-loading spin (AppButton, TbTagPanel)</span>
        </div>
        <v-progress-linear indeterminate color="accent" />
      </Spec>

      <Spec name="Notices, snackbar, alert" where="widgets/NoticeHost.vue · ImageGrid (v-snackbar) · SnapshotsSection (v-alert)">
        <div class="di-row">
          <AppButton size="sm" @click="pushNotices">Push four notices</AppButton>
          <AppButton size="sm" @click="snack = true">Open v-snackbar</AppButton>
        </div>
        <v-alert variant="tonal" density="compact" type="warning" class="di-alert">
          A tonal compact alert, the only v-alert in the app.
        </v-alert>
        <SettingsInfoCard>
          An info card: the settings dialog's quieter way to say the same thing.
        </SettingsInfoCard>
        <div class="empty-state">An empty state, App.css .empty-state</div>
        <v-snackbar v-model="snack" location="bottom" color="surface" elevation="4">
          12 pictures could not be restored.
          <template #actions>
            <v-btn variant="text" @click="snack = false">Dismiss</v-btn>
          </template>
        </v-snackbar>
      </Spec>

      <Spec name="ProgressOverlay" where="widgets/ProgressOverlay.vue · ImageGrid, CharacterEditor, DuplicateQueue">
        <div class="di-row">
          <div class="di-stage">
            <ProgressOverlay
              visible
              status="running"
              message="Tagging 120 pictures"
              :percent="42"
              :count="50"
              :total="120"
              abort-label="Stop"
            />
          </div>
          <div class="di-stage">
            <ProgressOverlay
              visible
              status="running"
              message="Scanning"
              indeterminate
            />
          </div>
          <div class="di-stage">
            <ProgressOverlay
              visible
              status="failed"
              message="Tagging 120 pictures"
              :percent="42"
            />
          </div>
        </div>
      </Spec>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="surfaces" class="di-section">
      <h2 class="di-h2">Menus, popovers, dialogs</h2>
      <p class="di-note">
        <code>AppDialog</code> 22 · raw <code>v-dialog</code> 22 · <code>v-menu</code>
        33 · <code>v-tooltip</code> 11 versus 242 <code>title=</code> attributes.
      </p>

      <Spec name="The toolbar popover shell" where="App.css .tbm · Sort / Filter / Grid view / Show / Group by">
        <div class="di-row di-row--panel">
          <div class="tbm di-tbm">
            <span class="tbm-caret tbm-caret--start"></span>
            <div class="tbm-header">
              <v-icon size="18" class="tbm-header-icon">mdi-filter-variant</v-icon>
              <span class="tbm-title">Filter</span>
              <span class="tbm-spacer"></span>
              <span class="tbm-count">1,284 matches</span>
              <button type="button" class="tbm-action tbm-action--secondary tbm-btn--compact">Clear</button>
            </div>
            <div class="tbm-section">
              <span class="tbm-label">Rating</span>
              <div class="tbm-btngroup">
                <button type="button" class="tbm-btn tbm-btn--on">Any</button>
                <button type="button" class="tbm-btn">★ 3+</button>
                <button type="button" class="tbm-btn">Unrated</button>
              </div>
            </div>
            <div class="tbm-section">
              <span class="tbm-label">Kind</span>
              <div class="tbm-check-grid">
                <label class="tbm-check"><input type="checkbox" checked /> Images</label>
                <label class="tbm-check"><input type="checkbox" checked /> Videos</label>
              </div>
            </div>
            <div class="tbm-section">
              <span class="tbm-label">Minimum width</span>
              <div class="di-row">
                <input class="tbm-num" value="1024" />
                <span class="tbm-mono">px</span>
              </div>
            </div>
            <div class="tbm-footer">Enter applies · Esc closes</div>
          </div>
        </div>
      </Spec>

      <Spec name="v-menu with v-list" where="TrainingRuns, FolderMappingTreeStep · 33 v-menu tags, most wrap a hand-rolled panel">
        <div class="di-row">
          <v-menu location="bottom end">
            <template #activator="{ props: p }">
              <v-btn v-bind="p" size="small" variant="text" append-icon="mdi-menu-down">Run…</v-btn>
            </template>
            <v-list density="compact">
              <v-list-item title="Resume" prepend-icon="mdi-play" />
              <v-list-item title="Duplicate" prepend-icon="mdi-content-copy" />
              <v-list-item title="Loading…" disabled />
            </v-list>
          </v-menu>
          <v-tooltip text="A v-tooltip, location top" location="top">
            <template #activator="{ props: p }">
              <AppButton v-bind="p" size="sm">Hover for v-tooltip</AppButton>
            </template>
          </v-tooltip>
          <AppButton size="sm" title="A native title tooltip">Hover for title=</AppButton>
        </div>
      </Spec>

      <Spec name="AppDialog versus raw v-dialog + v-card" where="widgets/AppDialog.vue (22) · FolderEditor, ShareDialog, RestoreConfirmDialog… (22)">
        <div class="di-row">
          <AppButton variant="primary" size="sm" @click="dlg = true">Open AppDialog</AppButton>
          <AppButton size="sm" @click="rawDlg = true">Open raw v-dialog</AppButton>
        </div>
        <AppDialog
          :open="dlg"
          title="Rename set"
          subtitle="Summer 2025"
          :width="440"
          @close="dlg = false"
          @accept="dlg = false"
        >
          <template #header-right>
            <HelpTip label="Rename" reason="Renames the set everywhere." />
          </template>
          <div class="di-form">
            <AppInput v-model="txt" label="Name" />
            <SettingsInfoCard>Pictures stay where they are.</SettingsInfoCard>
          </div>
          <template #footer>
            <AppButton key-hint="esc" @click="dlg = false">Cancel</AppButton>
            <AppButton variant="primary_green" key-hint="enter" @click="dlg = false">Save</AppButton>
          </template>
        </AppDialog>
        <v-dialog v-model="rawDlg" max-width="480">
          <v-card>
            <v-card-title>Edit folder</v-card-title>
            <v-card-text>
              <v-text-field v-model="txt" label="Label" variant="filled" density="comfortable" hide-details />
            </v-card-text>
            <v-card-actions>
              <v-btn variant="outlined" color="error" size="small">Delete</v-btn>
              <v-spacer />
              <v-btn variant="text" @click="rawDlg = false">Cancel</v-btn>
              <v-btn color="primary" variant="flat" @click="rawDlg = false">Save</v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>
      </Spec>

      <Spec name="Grid action pill (search + selection halves)" where="panels/GridActionPill.vue · bottom-anchored over the grid">
        <div class="di-stage di-stage--pill">
          <GridActionPill search-active selection-active>
            <template #search>
              <span class="di-pillhalf">
                <v-icon size="18">mdi-magnify</v-icon>
                <span>48 matches for “sunset”</span>
                <AppButton variant="ghost" size="sm" key-hint="esc">Clear</AppButton>
              </span>
            </template>
            <template #selection>
              <span class="di-pillhalf">
                <button type="button" class="selbar-count">12 selected</button>
                <button type="button" class="selbar-btn"><v-icon size="18">mdi-account-plus-outline</v-icon></button>
                <button type="button" class="selbar-btn selbar-btn--danger"><v-icon size="18">mdi-delete-outline</v-icon></button>
              </span>
            </template>
          </GridActionPill>
        </div>
      </Spec>
    </section>

    <!-- ══════════════════════════════════════════════════════════════════ -->
    <section id="settings" class="di-section">
      <h2 class="di-h2">Settings layout primitives</h2>
      <p class="di-note">
        <code>SettingsSection</code> 23 · <code>SettingsRow</code> 11 ·
        <code>SettingsFieldBlock</code> 3 · <code>SettingsTwoCol</code> 4 ·
        <code>SettingsChipGrid</code> 3.
      </p>
      <div class="di-settings">
        <SettingsSection title="Appearance" desc="How the library looks." first>
          <template #action>
            <AppButton size="sm" variant="ghost" icon-left="refresh">Reset</AppButton>
          </template>
          <SettingsRow label="Theme" sub="Dark is the default for a picture app.">
            <AppSelect v-model="themeSel" label="Theme" hide-label compact :options="themeOptions" />
          </SettingsRow>
          <SettingsRow label="Show file names" sub="Under each tile.">
            <v-switch v-model="on" color="accent" density="compact" hide-details />
          </SettingsRow>
          <SettingsRow label="Unavailable" sub="Needs a GPU." dim>
            <HelpTip label="GPU" reason="This machine has no CUDA device." />
          </SettingsRow>
        </SettingsSection>
        <SettingsSection title="Thumbnails">
          <SettingsTwoCol>
            <SettingsFieldBlock title="Size" desc="Pixels on the long edge.">
              <AppStepper v-model="num" :min="64" :max="1024" :step="32" />
            </SettingsFieldBlock>
            <SettingsFieldBlock title="Quality" desc="JPEG quality.">
              <SettingsSliderRow v-model="num" :min="0" :max="100" suffix="%" />
            </SettingsFieldBlock>
          </SettingsTwoCol>
          <SettingsChipGrid empty="No tags yet.">
            <SettingsChip label="landscape" meta="120" />
            <SettingsChip label="portrait" meta="88" />
            <SettingsChip label="macro" meta="3" />
          </SettingsChipGrid>
        </SettingsSection>
      </div>
    </section>

    <NoticeHost />
  </div>
</template>

<script setup>
import { computed, h, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useTheme } from "vuetify";

import { useNoticeStore } from "../../stores/useNoticeStore";
import { rememberTheme } from "../../utils/themeMemory";
import GridActionPill from "../panels/GridActionPill.vue";
import TbOverflowMenu from "../panels/TbOverflowMenu.vue";
import ReviewSticker from "../reviews/ReviewSticker.vue";
import SettingsChip from "../settings/SettingsChip.vue";
import SettingsChipGrid from "../settings/SettingsChipGrid.vue";
import SettingsFieldBlock from "../settings/SettingsFieldBlock.vue";
import SettingsInfoCard from "../settings/SettingsInfoCard.vue";
import SettingsRow from "../settings/SettingsRow.vue";
import SettingsSection from "../settings/SettingsSection.vue";
import SettingsSliderRow from "../settings/SettingsSliderRow.vue";
import SettingsTwoCol from "../settings/SettingsTwoCol.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppStepper from "../widgets/AppStepper.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import DedupConfidencePill from "../widgets/DedupConfidencePill.vue";
import DedupScopePill from "../widgets/DedupScopePill.vue";
import DedupThresholdControl from "../widgets/DedupThresholdControl.vue";
import DedupWhyPills from "../widgets/DedupWhyPills.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import HelpTip from "../widgets/HelpTip.vue";
import ModelMark from "../widgets/ModelMark.vue";
import NoticeHost from "../widgets/NoticeHost.vue";
import ProgressOverlay from "../widgets/ProgressOverlay.vue";
import StackBadge from "../widgets/StackBadge.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import TelemetryOptionMark from "../widgets/TelemetryOptionMark.vue";

// One specimen block: a caption naming the control and where it lives, then
// whatever the slot renders. Inline so the page stays one file.
const Spec = {
  props: { name: String, where: String },
  setup(props, { slots }) {
    return () =>
      h("div", { class: "di-spec" }, [
        h("div", { class: "di-spec__cap" }, [
          h("span", { class: "di-spec__name" }, props.name),
          h("span", { class: "di-spec__where" }, props.where),
        ]),
        h("div", { class: "di-spec__body" }, slots.default?.()),
      ]);
  },
};

const sections = [
  { id: "tokens", label: "Tokens" },
  { id: "buttons", label: "Buttons" },
  { id: "inputs", label: "Inputs" },
  { id: "toggles", label: "Toggles" },
  { id: "pills", label: "Pills & badges" },
  { id: "feedback", label: "Feedback" },
  { id: "surfaces", label: "Menus & dialogs" },
  { id: "settings", label: "Settings layout" },
];

const theme = useTheme();
const route = useRoute();
const isDark = computed(() => theme.global.current.value.dark);
const colors = computed(() => theme.global.current.value.colors);

function setTheme(mode) {
  theme.global.name.value =
    mode === "light" ? "pixlStashLight" : "pixlStashDark";
  rememberTheme(mode);
}
function toggleTheme() {
  setTheme(isDark.value ? "light" : "dark");
}
onMounted(() => {
  const q = route.query.theme;
  if (q === "light" || q === "dark") setTheme(q);
});

const colorGroups = [
  {
    name: "Canvas and chrome",
    keys: [
      "background",
      "surface",
      "panel",
      "sidebar",
      "toolbar",
      "input-background",
      "cancel-button",
      "dark-surface",
      "border",
      "divider",
    ],
  },
  {
    name: "Action fill tier",
    keys: ["accent", "accent-bright", "primary", "secondary", "tertiary", "sidebar-hover"],
  },
  { name: "Status", keys: ["error", "warning", "success", "info"] },
  {
    name: "Foregrounds on a dark surface",
    keys: [
      "dark-surface-primary",
      "dark-surface-error",
      "dark-surface-warning",
      "dark-surface-success",
      "dark-surface-info",
    ],
  },
  { name: "Folder levels", keys: ["level-1", "level-2", "level-3", "level-4"] },
];

const typeRamp = [
  { token: "2xs", px: "11px", use: "Uppercase section labels, badge counts" },
  { token: "xs", px: "12px", use: "Captions, metadata, dense secondary text" },
  { token: "sm", px: "13px", use: "Secondary body, toolbar labels" },
  { token: "base", px: "14px", use: "Default body and controls" },
  { token: "md", px: "16px", use: "Emphasised body, dialog body" },
  { token: "lg", px: "18px", use: "Card titles, dialog headings" },
  { token: "xl", px: "22px", use: "View titles" },
  { token: "2xl", px: "28px", use: "Login, startup, empty-state display" },
];

const appBtnVariants = ["primary", "primary_green", "secondary", "danger", "ghost"];

const txt = ref("");
const sel = ref("date");
const multi = ref(["date"]);
const selOptions = [
  { label: "Date added", value: "date" },
  { label: "Rating", value: "rating" },
  { label: "File name", value: "name" },
];
const themeSel = ref("dark");
const themeOptions = [
  { label: "Dark", value: "dark" },
  { label: "Light", value: "light" },
];
const num = ref(42);
const on = ref(true);
const tab = ref("local");
const shellFormat = ref("linux");
const snack = ref(false);
const dlg = ref(false);
const rawDlg = ref(false);

const whyPills = [
  { text: "98% visual match", against: false },
  { text: "Same capture time", against: false },
  { text: "Different resolution", against: true },
];

const stackBadges = [
  { label: "plain", props: { count: 4 } },
  { label: "unresolved", props: { count: 3, unresolved: true } },
  { label: "tinted", props: { count: 6, tint: "#de7ad0" } },
  { label: "flagged", props: { count: 5, flagged: true } },
  { label: "expanded", props: { count: 4, expanded: true } },
  { label: "dense", props: { count: 9, dense: true } },
];

const modelRow = {
  display_name: "Portra Film Look",
  filename: "portra_v2.safetensors",
  base_model: "SDXL",
};
const ringNone = {
  style: "none", hue: "", type: "", id: null, icon: "", iconHue: "", label: "Unassigned", count: 0,
};
const ringThick = { ...ringNone, style: "thick", hue: "#bb3566", label: "Anna (person)", count: 1 };
const ringDashed = { ...ringNone, style: "dashed", hue: "#46707a", label: "Summer (set)", count: 1 };
const ringDouble = { ...ringNone, style: "double", hue: "#567309", label: "Two assignments", count: 2 };

const notices = useNoticeStore();
function pushNotices() {
  notices.push({ level: "info", text: "Import started for 120 pictures." });
  notices.push({ level: "success", text: "12 pictures tagged." });
  notices.push({
    level: "warning",
    text: "3 files were skipped because they are not images.",
  });
  notices.push({
    level: "error",
    text: "The tagger could not start.",
    action: { label: "Retry", handler: () => {} },
  });
}
</script>

<style scoped>
.di {
  /* :root is overflow:hidden and #app is a fixed 100vh, so this is the scroller. */
  height: 100vh;
  box-sizing: border-box;
  overflow-y: auto;
  padding: var(--space-6) var(--space-7) var(--space-9);
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  font-family: var(--font-ui);
  font-size: var(--text-base);
}
.di code {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  padding: 0 var(--space-1);
  background: rgba(var(--v-theme-on-background), 0.08);
  border-radius: var(--radius-sm);
}
.di-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-5);
  flex-wrap: wrap;
}
.di-title {
  margin: var(--space-2) 0;
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}
.di-lede {
  max-width: 64ch;
  margin: 0;
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}
.di-nav {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin: var(--space-5) calc(var(--space-7) * -1) 0;
  padding: var(--space-3) var(--space-7);
  background: rgb(var(--v-theme-background));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-sm);
}
.di-nav a {
  color: rgb(var(--v-theme-accent));
  text-decoration: none;
}
.di-section {
  padding-top: var(--space-7);
  scroll-margin-top: var(--space-8);
}
.di-h2 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}
.di-h3 {
  margin: var(--space-6) 0 var(--space-3);
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}
.di-note {
  margin: 0 0 var(--space-5);
  max-width: 80ch;
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  font-size: var(--text-sm);
}

/* Specimen block */
.di-spec {
  margin-bottom: var(--space-6);
}
.di-spec :deep(.di-spec__cap) {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2) var(--space-4);
  margin-bottom: var(--space-3);
}
.di-spec :deep(.di-spec__name) {
  font-weight: var(--weight-semibold);
}
.di-spec :deep(.di-spec__where) {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}
.di-spec :deep(.di-spec__body) {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}
.di-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}
/* Popover controls are authored against `panel`, so show them on it. */
.di-row--panel {
  padding: var(--space-4);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
}
.di-row--tiles {
  align-items: flex-start;
}
.di-form {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-4);
  align-items: end;
}
.di-cap {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}
.di-sep {
  width: 1px;
  height: var(--rule-h);
  background: rgb(var(--v-theme-divider));
}

/* Tokens */
.di-swatch-group {
  margin-bottom: var(--space-5);
}
.di-swatches {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-2);
}
.di-swatch {
  width: 104px;
}
.di-swatch__chip {
  height: 48px;
  display: flex;
  align-items: flex-end;
  padding: var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  font-size: var(--text-2xs);
}
.di-swatch__name {
  margin-top: var(--space-1);
  font-size: var(--text-xs);
}
.di-swatch__hex {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}
.di-type__row {
  display: flex;
  align-items: baseline;
  gap: var(--space-5);
  padding: var(--space-2) 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}
.di-type__meta {
  flex: 0 0 11rem;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}
.di-grid-3 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-6);
}
.di-spacing__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 20px;
}
.di-spacing__bar {
  height: 12px;
  background: rgb(var(--v-theme-accent));
}
.di-radii,
.di-elev {
  display: flex;
  gap: var(--space-4);
  padding: var(--space-3);
}
.di-radius {
  width: 64px;
  height: 48px;
  display: grid;
  place-items: center;
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-surface));
  font-size: var(--text-xs);
}
.di-elev__card {
  width: 64px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  font-size: var(--text-xs);
}

/* Stages for absolutely-positioned controls */
.di-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 var(--space-3);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-radius: var(--radius-sm);
}
.di-toolbar__split {
  display: inline-flex;
}
.di-menu {
  min-width: 220px;
  padding: var(--space-2) 0;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 6px;
  box-shadow: var(--elevation-3);
}
.di-tbm {
  width: 360px;
  margin-top: var(--space-3);
}
.di-stage {
  position: relative;
  flex: 1 1 260px;
  min-height: 150px;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.di-stage--pill {
  min-height: 96px;
}
.di-pillhalf {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  white-space: nowrap;
}
.di-tile {
  position: relative;
  width: 96px;
  height: 96px;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    rgb(var(--v-theme-tertiary)),
    rgb(var(--v-theme-secondary))
  );
}
.di-tile--wide {
  width: 160px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.di-tile__cap {
  position: absolute;
  left: 0;
  right: 0;
  bottom: var(--space-2);
  text-align: center;
  font-size: var(--text-2xs);
  color: #f7f1ea;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
}
.di-tile__badge--count {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  min-width: var(--badge-size);
  height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  display: grid;
  place-items: center;
}
.di-tile__badge--dot {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
}
.di-tmark {
  color: rgb(var(--v-theme-on-surface));
}
.di-range {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}
.di-alert {
  font-size: var(--text-sm);
}
.di-settings {
  max-width: 720px;
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}
</style>
