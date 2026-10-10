<template>
  <AppInspector
    v-model="tab"
    :class="['wftab', { 'wftab--workflow': tab === 'workflow' }]"
    label="Inspector"
    :open="sidebarStore.workflowInspectorOpen"
    :tabs="tabs"
  >
    <!-- The task manager, last tab and on its own: what the app is working on
         is not part of a workflow, so it replaces the body rather than sitting
         under it. Here so that a run started from this screen can be watched
         from this screen. -->
    <TasksPanel v-if="tab === 'tasks'" />

    <p v-else-if="!card && !multiple" class="wftab-empty">
      Pick a workflow to see what it is made of.
    </p>

    <!-- Before the multiple-selection body: a selection of several has a
         Recipes answer of its own, the union of their recipes, which is what
         the owner is asking for by selecting them. -->
    <WorkflowRecipesTab
      v-else-if="showingRecipes"
      :workflow-ids="recipeKeys"
      :workflow-name="recipesName"
      @defaults-changed="takeDefaults"
    />

    <!-- Several selected: the tab says so, and the VERBS ARE THE PILL'S.
         They were here first, as two buttons of this tab's own; #1455 gave
         the grid a selection bar that offers the same two and eight more, and
         two surfaces deriving the same verb independently disagreed within a
         week — this Hide was hardcoded `Hide` while the pill's is a toggle
         reading Unhide on an all-hidden selection. Two live controls on one
         screen, one of them saying the wrong thing about what it is about to
         do.

         So one owner. The pill is always on screen when this block is (both
         are gated on a selection, and it is docked over the grid this rail
         sits beside), so nothing became unreachable — and the reader is told
         where the verbs are rather than left to find them. -->
    <template v-else-if="multiple">
      <div class="inspector-section">
        <span class="section-label">Selected</span>
        <p class="wftab-title">
          {{ store.selectedKeys.length }} workflows selected
        </p>
        <p class="wftab-note wftab-quiet">
          What you can do with them is on the bar at the bottom of the grid, or
          under a right-click on any of them.
        </p>
      </div>
    </template>

    <!-- A workflow the grid does not list, while its read is out. -->
    <p v-else-if="!card" class="wftab-empty">
      {{
        detailFailed
          ? "Could not read this workflow just now."
          : "Reading this workflow…"
      }}
    </p>

    <template v-else>
      <div class="inspector-section wftab-head">
        <BreakableTitle class="wftab-title" :name="card.name" />
        <p class="wftab-sub">
          <button
            v-if="card.picture_count"
            class="wftab-pictures"
            type="button"
            data-testid="wftab-show-pictures"
            :aria-label="`Show all ${picturesLabel}`"
            @click="showPictures(card)"
          >
            {{ picturesLabel }}</button
          ><template v-else>{{ picturesLabel }}</template>
          <!-- Where the defaults come from is the whole workflow's, so it sits
               here and not inside any one panel. Absent when nothing is
               rated. -->
          <WorkflowStarChart
            v-if="ratingCounts"
            :counts="ratingCounts"
            :sampled="Boolean(defaultRecipe?.sampled) && !card.manual"
          />
        </p>
        <p
          v-if="versionLine"
          class="wftab-sublabel"
          data-testid="wftab-version"
        >
          {{ versionLine }}
        </p>
      </div>

      <!-- This workflow's last run ended without a picture (#1839). First in
           the body, so it is on screen with the name whatever the panels
           below scroll to, and kept until dismissed or the workflow runs
           again: the Run popup closes on submit, so this and the card's chip
           are where the run was started. `status`, not `alert`: the panel is
           mounted again each time the workflow is selected, and an alert
           would interrupt with the same sentence every time. -->
      <section
        v-if="runFailure"
        class="wftab-panel wftab-run-failure"
        :class="{ 'wftab-run-failure--stopped': runFailure.stopped }"
        role="status"
        data-testid="wftab-run-failure"
      >
        <div class="wftab-sec-head">
          <span class="section-label" role="heading" aria-level="3">{{
            runFailure.stopped ? "Last run stopped" : "Last run failed"
          }}</span>
          <AppButton
            size="sm"
            variant="ghost"
            data-testid="wftab-run-failure-dismiss"
            @click="dismissRunFailure"
          >
            Dismiss
          </AppButton>
        </div>
        <p class="wftab-note wftab-run-failure__message">
          {{ runFailure.message }}
        </p>
      </section>

      <!-- What a run starts from (#1653), as three bordered panels: Models,
           LoRAs and Parameters, each with its verb in its own head. Inside a
           panel, label-and-value rows on a 64px label column; a row says
           where its value came from only when it is the exception ("Yours",
           "in 31 of 40"). -->
      <section class="wftab-panel" data-testid="wftab-default-recipe">
        <div class="wftab-sec-head">
          <span class="section-label" role="heading" aria-level="3"
            >Models</span
          >
          <AppButton
            size="sm"
            variant="ghost"
            data-testid="wftab-clone-onto-set"
            :disabled="chainNoGraph"
            :aria-describedby="chainNoGraph ? 'wftab-chain-reason' : undefined"
            @click="store.requestCloneOntoSet(selectedKey)"
          >
            Clone onto a set…
          </AppButton>
        </div>
        <!-- The list card's `default_recipe` is null, so until the detail
             lands there is no default to show: its base-card slots are not
             one. -->
        <p v-if="detailPending" class="wftab-note wftab-quiet">
          Reading its default recipe…
        </p>
        <p
          v-else-if="detailFailed"
          class="wftab-note wftab-quiet"
          data-testid="wftab-recipe-failed"
        >
          Could not read its default recipe just now.
          <AppButton
            size="sm"
            variant="ghost"
            data-testid="wftab-recipe-retry"
            @click="loadDetail(selectedKey)"
          >
            Retry
          </AppButton>
        </p>
        <template v-if="detail">
        <div class="wftab-field wftab-field--top" data-testid="wftab-row-checkpoint">
          <span class="wftab-labelcol">
            <span class="wftab-label">{{
              checkpointCount > 1 ? "Checkpoints" : "Checkpoint"
            }}</span>
            <span v-if="checkpointEdited" class="wftab-yours"
              >Yours</span
            >
          </span>
          <div class="wftab-col">
          <!-- Missing: ComfyUI does not have the file (the run pre-flight's
               answer), or - when ComfyUI cannot be asked - the card has no
               name for it. Always with the FILE it is missing, as its name
               without folders; the whole recorded value is the tooltip. -->
          <div v-if="detail && checkpointIsMissing" class="wftab-missing">
            <p class="wftab-warn">
              <v-icon size="16">mdi-alert-outline</v-icon>
              Checkpoint missing
            </p>
            <p
              v-if="missingCheckpointFile"
              class="wftab-note wftab-quiet"
              data-testid="wftab-missing-file"
            >
              <span class="wftab-file"
                ><Tooltip :text="missingCheckpointFile" activator="parent" />{{
                  fileName(missingCheckpointFile)
                }}</span
              >{{ preflightAnswered ? " is not installed in ComfyUI." : "" }}
            </p>
            <p v-else class="wftab-note wftab-quiet">
              No file name was kept for it anywhere.
            </p>
            <!-- The pick is saved on the workflow, so an offer that is not
                 of the missing one's base model says so before it. -->
            <p
              v-if="checkpointUnmatched"
              class="wftab-note wftab-quiet"
              data-testid="wftab-replace-unmatched"
            >
              {{ UNMATCHED_REPLACEMENTS_TEXT }}
            </p>
            <!-- The fix: another shelf model in its place. The card keeps its
                 pictures, and what the replacement makes is filed on it. -->
            <AppSelect
              v-if="replaceOptions.length"
              model-value=""
              label="Replace with a model from your shelf"
              hide-label
              :options="replaceOptions"
              :disabled="busy === 'model-fix'"
              data-testid="wftab-replace-model"
              @update:model-value="replaceCheckpoint"
            />
            <p
              v-else-if="checkpointNoReplacement"
              class="wftab-note wftab-quiet"
              data-testid="wftab-no-replacement-checkpoint"
            >
              {{ checkpointNoReplacement }}
            </p>
            <!-- The replacement has gone missing too: say what it was, and
                 keep the way back reachable. Choosing another above replaces
                 the original, never the replacement. -->
            <p
              v-if="replacementMissing"
              class="wftab-note wftab-quiet"
              data-testid="wftab-fix-missing"
            >
              Replaced by {{ fileName(checkpointFix.now) }}, which is missing
              too.
              <AppButton
                variant="ghost"
                size="sm"
                icon-only
                icon-left="undo"
                :tooltip="`Undo: load ${fileName(checkpointFix.was)} again`"
                :disabled="busy === 'model-fix'"
                @click="replaceCheckpoint(null)"
              />
            </p>
          </div>
          <!-- Replaced by the owner: what it loads now, flagged, and the
               original a hover away, because this is not the workflow as its
               pictures were made. -->
          <div
            v-else-if="checkpointFix"
            class="wftab-fixed"
            data-testid="wftab-fixed-model"
          >
            <span class="wftab-value">
              <v-icon size="16" class="wftab-fixed-flag" aria-hidden="true"
                >mdi-alert-outline</v-icon
              >
              <Tooltip
                :text="`Replaced. This workflow originally used ${fileName(checkpointFix.was)}`"
                activator="parent"
              />
              {{ fileName(checkpointFix.now) }}
              <span class="visually-hidden"
                >, replaced. This workflow originally used
                {{ fileName(checkpointFix.was) }}</span
              >
            </span>
            <AppButton
              variant="ghost"
              size="sm"
              icon-only
              icon-left="undo"
              data-testid="wftab-undo-fix"
              :tooltip="`Undo: load ${fileName(checkpointFix.was)} again`"
              :disabled="busy === 'model-fix'"
              @click="replaceCheckpoint(null)"
            />
          </div>
          <span v-else-if="checkpointLabel" class="wftab-value">{{
            checkpointLabel
          }}</span>
          <!-- The card has no name for it, but the graph a run submits does:
               that is the file, installed as far as anybody knows. -->
          <span v-else-if="graphBaseModel" class="wftab-value"
            ><Tooltip :text="graphBaseModels" activator="parent" />{{
              detail.graph_base_models.map(fileName).join(" + ")
            }}</span
          >
          <!-- The graph a run would submit was read and loads no base model:
               an upscaler, say. A fact, so it is said as one. -->
          <span v-else-if="graphLoadsNone" class="wftab-value wftab-quiet">
            None in this workflow
          </span>
          <!-- A recipe-less card's rows are what its file gave up, so an
               empty one is "not read", never a claim that it has none. -->
          <span v-else-if="checkpointIsUnread" class="wftab-value wftab-quiet">
            Not read from its file
          </span>
          <!-- Only when nothing anywhere names a base model: not the card, not
               the graph a run would submit, not the pre-flight. -->
          <span v-else class="wftab-value wftab-quiet">Not recorded</span>
          <!-- Show N (§1.5): only below the workflow's own count, which the
               head's link already shows. -->
          <div
            v-for="shown in checkpointShows"
            :key="shown.name"
            class="wftab-meta"
          >
            <span class="wftab-coverage wftab-quiet">{{
              checkpointShows.length > 1 ? shown.label : ""
            }}</span>
            <AppButton
              size="sm"
              variant="ghost"
              data-testid="wftab-show-checkpoint"
              :aria-label="showLabel(shown.pictures, shown.label)"
              @click="showValue('model', shown.name, shown.label)"
            >
              Show {{ shown.pictures }}
            </AppButton>
          </div>
          <!-- The other checkpoints its pictures used: a disclosure, not a
               menu, because each is a row with its own Show N. -->
          <button
            v-if="otherCheckpoints.length"
            type="button"
            class="wftab-others"
            data-testid="wftab-other-checkpoints"
            aria-controls="wftab-other-checkpoints"
            :aria-expanded="othersOpen ? 'true' : 'false'"
            @click="othersOpen = !othersOpen"
          >
            <v-icon size="16" aria-hidden="true">{{
              othersOpen ? "mdi-chevron-down" : "mdi-chevron-right"
            }}</v-icon>
            +{{ otherCheckpoints.length }}
            {{ otherCheckpoints.length === 1 ? "other" : "others" }}
          </button>
          <ul
            v-if="othersOpen && otherCheckpoints.length"
            id="wftab-other-checkpoints"
            class="wftab-others-list"
          >
            <li v-for="other in otherCheckpoints" :key="other.name">
              <span class="wftab-others-name"
                ><Tooltip :text="other.name" activator="parent" />{{
                  other.label
                }}</span
              >
              <AppButton
                size="sm"
                variant="ghost"
                :aria-label="showLabel(other.pictures, other.label)"
                @click="showValue('model', other.name, other.label)"
              >
                Show {{ other.pictures }}
              </AppButton>
            </li>
          </ul>
          </div>
        </div>
        <!-- The VAE and the text encoders (#1596): missing and replaced the
             way the Checkpoint row is, one entry per file. -->
        <div
          v-for="row in supportRows"
          :key="row.kind"
          class="wftab-field"
          :data-testid="`wftab-row-${row.kind}`"
        >
          <span class="wftab-label">{{ row.label }}</span>
          <div class="wftab-entries">
            <template v-for="entry in row.entries" :key="entry.id">
              <div v-if="entry.file" class="wftab-missing">
                <p class="wftab-warn">
                  <v-icon size="16">mdi-alert-outline</v-icon>
                  {{ row.missingText }}
                </p>
                <p class="wftab-note wftab-quiet">
                  <span class="wftab-file"
                    ><Tooltip :text="entry.file" activator="parent" />{{
                      fileName(entry.file)
                    }}</span
                  >
                  is not installed in ComfyUI.
                </p>
                <AppSelect
                  v-if="entry.options.length"
                  model-value=""
                  :label="`Replace with a ${row.noun} from your shelf`"
                  hide-label
                  :options="entry.options"
                  :disabled="busy === 'model-fix'"
                  :data-testid="`wftab-replace-${row.kind}`"
                  @update:model-value="
                    (now) => replaceModel(row.kind, entry.file, now)
                  "
                />
                <p
                  v-else-if="entry.noReplacement"
                  class="wftab-note wftab-quiet"
                  :data-testid="`wftab-no-replacement-${row.kind}`"
                >
                  {{ entry.noReplacement }}
                </p>
                <p
                  v-if="entry.fix"
                  class="wftab-note wftab-quiet"
                  :data-testid="`wftab-fix-missing-${row.kind}`"
                >
                  Replaced by {{ fileName(entry.fix.now) }}, which is missing
                  too.
                  <AppButton
                    variant="ghost"
                    size="sm"
                    icon-only
                    icon-left="undo"
                    :tooltip="`Undo: load ${entry.originals} again`"
                    :disabled="busy === 'model-fix'"
                    :data-testid="`wftab-undo-missing-${row.kind}`"
                    @click="replaceModel(row.kind, entry.file, null)"
                  />
                </p>
              </div>
              <div
                v-else-if="entry.fix"
                class="wftab-fixed"
                :data-testid="`wftab-fixed-${row.kind}`"
              >
                <span class="wftab-value">
                  <v-icon size="16" class="wftab-fixed-flag" aria-hidden="true"
                    >mdi-alert-outline</v-icon
                  >
                  <Tooltip
                    :text="`Replaced. This workflow originally used ${fileName(entry.fix.was)}`"
                    activator="parent"
                  />
                  {{ fileName(entry.fix.now) }}
                  <span class="visually-hidden"
                    >, replaced. This workflow originally used
                    {{ fileName(entry.fix.was) }}</span
                  >
                </span>
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon-only
                  icon-left="undo"
                  :data-testid="`wftab-undo-${row.kind}`"
                  :tooltip="`Undo: load ${fileName(entry.fix.was)} again`"
                  :disabled="busy === 'model-fix'"
                  @click="replaceModel(row.kind, entry.fix.was, null)"
                />
              </div>
              <span v-else class="wftab-value">{{ entry.text }}</span>
            </template>
          </div>
        </div>

        <!-- Read-only until the default can be written (F-3); the Run form
             skips a stage per run. Absent when the base graph has none. -->
        <div
          v-if="stageRows.length"
          class="wftab-field"
          data-testid="wftab-stages"
        >
          <span class="wftab-label">Stages</span>
          <span class="wftab-value"
            ><template v-for="(stage, index) in stageRows" :key="stage.key"
              ><span v-if="index" class="wftab-quiet"> · </span
              ><span v-if="stage.detail" :data-testid="`wftab-stage-${stage.key}`"
                ><Tooltip :text="stage.detail" activator="parent" />{{
                  stage.label
                }}</span
              ><template v-else>{{ stage.label }}</template
              >{{ stage.on ? "" : " off" }}</template
            ></span
          >
        </div>
        <p
          v-if="stageRows.some((stage) => !stage.on)"
          class="wftab-note wftab-quiet"
        >
          Off: most of its pictures ran without it.
        </p>
        </template>
      </section>

      <!-- The default recipe's LoRAs and the ones its pictures used besides,
           as one family with two kinds of member. "Edit LoRAs…" is in the
           head, beside what it edits, and is here even with no loader at all:
           an entry point that only exists for workflows that already have
           LoRAs is how adding the first one stays unreachable (#1478). -->
      <section class="wftab-panel" data-testid="wftab-loras">
        <div class="wftab-sec-head">
          <span
            ref="defaultHeading"
            class="section-label"
            role="heading"
            aria-level="3"
            tabindex="-1"
            >LoRAs</span
          >
          <AppButton
            size="sm"
            variant="ghost"
            data-testid="wftab-edit-loras"
            :disabled="chainNoGraph"
            :aria-describedby="chainNoGraph ? 'wftab-chain-reason' : undefined"
            @click="openEditLoras(selectedKey)"
          >
            Edit LoRAs…
          </AppButton>
        </div>
        <template v-if="detail">
        <!-- The default recipe's LoRAs, in the order the chain applies them.
             One that is not in every picture says how many it is in; one in
             every picture says nothing, and that silence means "all". A
             LoRA is here or in Also used, never both. -->
        <div
          v-for="lora in loraRows"
          :key="lora.id"
          class="wftab-field wftab-field--top"
          data-testid="wftab-default-lora"
        >
          <span class="wftab-labelcol">
            <span class="wftab-label">Default</span>
            <span v-if="lora.edited" class="wftab-yours">Yours</span>
          </span>
          <div class="wftab-col">
            <span class="wftab-value wftab-lora-value">
              <v-icon
                v-if="!lora.on_shelf"
                size="16"
                class="wftab-chain-flag"
                aria-hidden="true"
                >mdi-alert-outline</v-icon
              >
              <span class="wftab-chain-name"
                ><Tooltip v-if="lora.file" :text="lora.file" activator="parent" />{{
                  lora.label
                }}</span
              >
              <span v-if="!lora.on_shelf" class="visually-hidden"
                >, not on your model shelf</span
              >
              <span v-if="lora.strengthText" class="wftab-chain-strength">{{
                lora.strengthText
              }}</span>
            </span>
            <div v-if="lora.coverage || lora.show" class="wftab-meta">
              <span class="wftab-coverage wftab-quiet">{{ lora.coverage }}</span>
              <AppButton
                v-if="lora.show"
                size="sm"
                variant="ghost"
                :aria-label="showLabel(lora.show.pictures, lora.label)"
                @click="showValue('lora', lora.show.name, lora.label)"
              >
                Show {{ lora.show.pictures }}
              </AppButton>
            </div>
          </div>
        </div>
        </template>
        <p v-if="usingChain && chainPending" class="wftab-note wftab-quiet">
          Reading its LoRAs…
        </p>
        <p
          v-else-if="chainNoGraph"
          id="wftab-chain-reason"
          class="wftab-note wftab-quiet"
        >
          PixlStash has no graph for this workflow, so its LoRAs cannot be read
          or edited.
        </p>
        <p v-else-if="usingChain && chainFailed" class="wftab-note wftab-quiet">
          Could not read its LoRAs just now.
        </p>
        <p
          v-else-if="loraNote"
          class="wftab-note wftab-quiet"
          data-testid="wftab-shared-note"
        >
          {{ loraNote }}
        </p>
        <p
          v-else-if="chain && !chainLoaders.length"
          class="wftab-note wftab-quiet"
        >
          No LoRA loader. Editing adds the first one.
        </p>
        <!-- The LoRAs its pictures used that the default recipe does not
             load, as one pile under a hairline. Absent, not empty, when there
             are none. -->
        <template v-if="alsoUsed.length || summaryFailed">
          <div class="wftab-rule" aria-hidden="true"></div>
          <div v-if="alsoUsed.length" class="wftab-field" data-testid="wftab-changes">
            <span class="wftab-label">Also used</span>
            <WorkflowLoraPile
              :summary="pileSummary"
              :adding="busy.startsWith('default-lora:') ? busy.slice(13) : ''"
              @show="showLora"
              @add-default="addLoraToDefault"
            />
          </div>
          <p v-if="alsoUsed.length" class="wftab-note wftab-quiet">
            {{ pileNote }}
          </p>
          <p v-else class="wftab-note wftab-quiet" data-testid="wftab-changes">
            Could not read which LoRAs change between pictures just now.
          </p>
        </template>
      </section>

      <!-- Each parameter is set each run (asked for by the Run form) or fixed
           (every run uses the workflow's value; a recipe and an API or MCP
           override still win). Grouped by what it was when the workflow was
           opened, so a toggled row stays under the pointer and moves next
           time. -->
      <section class="wftab-panel" data-testid="wftab-parameters">
        <div class="wftab-sec-head">
          <span class="section-label" role="heading" aria-level="3"
            >Parameters</span
          >
          <!-- Any other setting of the graph's nodes, picked by node and made
               a row here. Offered once the detail is in: the write it ends in
               is whole-set, built from the rows on screen. -->
          <AppButton
            v-if="detail"
            size="sm"
            variant="ghost"
            icon-only
            icon-left="plus"
            tooltip="Expose a parameter…"
            :loading="busy === 'expose'"
            data-testid="wftab-expose"
            @click="exposeOpen = true"
          />
        </div>
        <p v-if="detailPending" class="wftab-note wftab-quiet">
          Reading its parameters…
        </p>
        <p v-else-if="detailFailed" class="wftab-note wftab-quiet">
          Could not read its parameters just now.
        </p>
        <template v-else-if="defaults.length">
          <template v-if="runDefaults.length">
            <p class="wftab-sublabel">Set each run</p>
            <WorkflowDefaultRow
              v-for="row in runDefaults"
              :key="row.label"
              :row="row"
              :busy="busy === `default:${row.label}`"
              :options="choicesFor(formOptions, row)"
              :node="nodeOf(row)"
              @toggle-pin="togglePin(row)"
              @reset="writeDefault(row)"
              @edit="(value) => writeDefault(row, { value })"
            />
          </template>
          <template v-if="fixedDefaults.length">
            <div
              v-if="runDefaults.length"
              class="wftab-rule"
              aria-hidden="true"
            ></div>
            <!-- The group is one element, label, rows and note, so "in the
                 Fixed group" is containment rather than document order. -->
            <div class="wftab-rows" data-testid="wftab-fixed-group">
              <!-- More than three fixed rows fold away behind their count. -->
              <details
                v-if="fixedDefaults.length > FIXED_SHOWN"
                class="wftab-disclose"
              >
                <summary class="wftab-sublabel">
                  Fixed <span class="num">· {{ fixedDefaults.length }}</span>
                </summary>
                <div class="wftab-rows">
                  <WorkflowDefaultRow
                    v-for="row in fixedDefaults"
                    :key="row.label"
                    :row="row"
                    :busy="busy === `default:${row.label}`"
                    :options="choicesFor(formOptions, row)"
                    :node="nodeOf(row)"
                    @toggle-pin="togglePin(row)"
                    @reset="writeDefault(row)"
                    @edit="(value) => writeDefault(row, { value })"
                  />
                </div>
              </details>
              <template v-else>
                <p class="wftab-sublabel">
                  Fixed <span class="num">· {{ fixedDefaults.length }}</span>
                </p>
                <WorkflowDefaultRow
                  v-for="row in fixedDefaults"
                  :key="row.label"
                  :row="row"
                  :busy="busy === `default:${row.label}`"
                  :options="choicesFor(formOptions, row)"
                  :node="nodeOf(row)"
                  @toggle-pin="togglePin(row)"
                  @reset="writeDefault(row)"
                  @edit="(value) => writeDefault(row, { value })"
                />
              </template>
              <p class="wftab-note wftab-quiet">
                Fixed values are used by every run.
              </p>
            </div>
          </template>
        </template>
        <p
          v-else-if="editorOnly"
          class="wftab-note wftab-quiet"
          data-testid="wftab-editor-only"
        >
          Parameters are not available yet: PixlStash could not convert this
          ComfyUI workflow. Open it in ComfyUI from here and it is converted
          there.
        </p>
        <p v-else class="wftab-note wftab-quiet">
          Nothing this workflow made records a setting yet, so it has no
          defaults to start from.
        </p>
      </section>

      <div class="inspector-section">
        <!-- The box is drawn only once THIS card's notes have arrived.
             `notesDraft` holds the last card read, so a box shown while the
             read is out is the previous workflow's text, and blurring it
             writes that text onto this one. -->
        <details class="wftab-disclose">
          <summary>Notes</summary>
          <textarea
            v-if="detail"
            v-model="notesDraft"
            class="wftab-notes"
            rows="4"
            aria-label="Notes about this workflow"
            @blur="saveNotes"
          ></textarea>
          <p v-else class="wftab-note wftab-quiet">
            {{
              detailFailed
                ? "Could not read this workflow's notes just now."
                : "Reading its notes…"
            }}
          </p>
        </details>
        <details class="wftab-disclose">
          <summary>
            Node names <span class="wftab-quiet">for ComfyUI users</span>
          </summary>
          <dl class="inspector-kv">
            <div v-for="slot in nodeNames" :key="slot.id">
              <dt>{{ slot.kind }}</dt>
              <dd class="wftab-mono">{{ slot.label || "—" }}</dd>
            </div>
          </dl>
        </details>
      </div>
    </template>

    <!-- The inspector's footer slot sits below the scrolling body, so Run…
         stays where the design puts it and never scrolls. -->
    <!-- The Workflow tab's, and only its: Recipes runs a recipe from its own
         row and Tasks is the app's business, so neither wants this footer. -->
    <template #footer>
      <div
        v-if="tab === 'workflow' && (card || multiple)"
        class="wftab-foot"
      >
        <AppButton
          ref="runButton"
          variant="primary"
          icon-left="play"
          block
          :aria-disabled="runTarget ? undefined : 'true'"
          :aria-describedby="runDescribedBy"
          @click="run"
        >
          Run…
        </AppButton>
        <!-- Beside Run…, as the ComfyUI mark: the ComfyUI-PixlStash node reads
             `?pixlstash_workflow=` and loads the graph, so without the node
             ComfyUI opens on whatever it had last. It opens what Run… runs
             (`runTarget`), and is refused rather than hidden otherwise, for
             Run…'s reason or a ComfyUI without the node. The tooltip is its
             accessible name. "A copy", because that is what it is: what
             ComfyUI saves comes back as a workflow of its own on the next
             pull, and this one keeps its graph. -->
        <AppButton
          v-if="canOpenComfyui"
          icon-only
          tooltip="Open a copy in ComfyUI"
          data-testid="wftab-open-comfyui"
          :aria-disabled="runTarget && !comfyuiLacksNode ? undefined : 'true'"
          :aria-describedby="openDescribedBy"
          @click="openInComfyui"
        >
          <template #icon="{ size }"><ComfyuiIcon :size="size" /></template>
        </AppButton>
        <v-menu
          v-if="card"
          v-model="menuOpen"
          location="top end"
          origin="bottom end"
          :offset="8"
        >
          <template #activator="{ props: menuProps }">
            <AppButton
              v-bind="menuProps"
              icon-left="dots-horizontal"
              icon-only
              tooltip="More"
              aria-haspopup="menu"
              :aria-expanded="menuOpen"
            />
          </template>
          <div class="tbm">
            <div class="tbm-section">
              <button class="wftab-item" type="button" @click="toggleHidden">
                <v-icon size="16">{{
                  detail?.hidden ? "mdi-eye-outline" : "mdi-eye-off-outline"
                }}</v-icon>
                {{ detail?.hidden ? "Unhide" : "Hide" }}
              </button>
            </div>
          </div>
        </v-menu>
        <p
          v-if="multiple && canOpenComfyui"
          id="wftab-open-reason"
          class="wftab-note wftab-quiet"
        >
          Open one workflow at a time
        </p>
        <p
          v-else-if="comfyuiLacksNode && canOpenComfyui"
          id="wftab-open-node-reason"
          class="wftab-note wftab-quiet"
        >
          Opening a workflow needs the
          <a
            class="wftab-link"
            :href="PIXLSTASH_PACK_URL"
            target="_blank"
            rel="noopener noreferrer"
            >ComfyUI-PixlStash</a
          >
          node in ComfyUI. Install or update it, then restart ComfyUI.
        </p>
        <p v-if="multiple" id="wftab-run-reason" class="wftab-note wftab-quiet">
          Run one workflow at a time
        </p>
      </div>
    </template>

    <!-- Keyed to the card it was OPENED on, not to the selection: a save
         selects the new card, and the dialog must not re-read the chain of
         the card it just wrote under the owner's feet. -->
    <EditLorasDialog
      v-if="editKey"
      :open="Boolean(editKey)"
      :workflow-id="editKey"
      :card-name="editName"
      :picture-count="editPictures"
      :origin-category="editOrigin"
      :drop-lora="editDrop"
      @close="closeEditLoras"
      @overwritten="onOverwritten"
    />
    <ExposeParameterDialog
      v-if="exposeOpen"
      :open="exposeOpen"
      :nodes="exposable"
      :pending="formPending"
      :failed="formFailed"
      @close="exposeOpen = false"
      @expose="exposeParameter"
    />
  </AppInspector>
</template>

<script setup>
// The Workflow tab of the Workflows grid's inspector (implementation plan §F3).
//
// Built as a sibling of the shipped shelf's own inspector rather than a
// reshaping of it, because every step of this feature had to leave the shelf
// on `/workflows` working; F1b (#1404) then deleted the shelf, its store and
// that inspector together, and this is the rail on `/workflows`.
//
// What the rail shows follows the SELECTION: one workflow, keyed by its `id`
// (#1623). Models and Defaults read the detail's `card.default_recipe`.

import { computed, nextTick, onBeforeUnmount, ref, toRaw, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import {
  getLoraChain,
  getLoraSummary,
  getWorkflowCard,
  getWorkflowFormInputs,
  patchWorkflowCard,
  preflightWorkflowRun,
  readModelSwap,
  setWorkflowDefaultLora,
  setWorkflowDefaults,
  setWorkflowModelFix,
  setWorkflowPins,
  workflowCoverUrl,
} from "../../api/workflows";
import { getPixlstashNode } from "../../api/comfyui";
import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import { useFilterStore } from "../../stores/useFilterStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { modelLabel } from "../../utils/filterChips";
import {
  EDIT_LORAS,
  formatStrength,
  loraBase,
  loraStem,
} from "../../utils/loraChain";
import { quantBadge } from "../../utils/modelShelf";
import { PIXLSTASH_PACK_URL, STAGE_LABELS } from "../../utils/runReasons";
import {
  checkpointMissing,
  baseModels,
  checkpointUnread,
  modelDisplayName,
  NO_REPLACEMENT_TEXT,
  outputCount,
  replacementOptions,
  UNMATCHED_REPLACEMENTS_TEXT,
} from "../../utils/workflowCard";
import {
  choicesFor,
  nodesByAddress,
  optionsByAddress,
  parameterAddress,
  setEachRun,
} from "../../utils/workflowPins";
import { pictureCount } from "../../utils/workflowSets";
import { formatUserDay } from "../../utils/utils";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import AppSelect from "../widgets/AppSelect.vue";
import BreakableTitle from "../widgets/BreakableTitle.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import EditLorasDialog from "../io/EditLorasDialog.vue";
import ExposeParameterDialog from "../io/ExposeParameterDialog.vue";
import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowDefaultRow from "./WorkflowDefaultRow.vue";
import WorkflowStarChart from "./WorkflowStarChart.vue";
import WorkflowLoraPile from "./WorkflowLoraPile.vue";
import WorkflowRecipesTab from "./WorkflowRecipesTab.vue";

/** Past this many, the Fixed group folds away behind "Fixed · N". */
const FIXED_SHOWN = 3;

const store = useWorkflowsStore();
const { showPictures, showLoraPictures, showValuePictures } =
  useWorkflowPictures();
const sidebarStore = useSidebarStore();
const notices = useNoticeStore();
const filterStore = useFilterStore();
/**
 * The desktop shell's bridge, when there is one. Its window opens only `https:`
 * outside the app and ComfyUI is plain `http:`, so on the desktop the link goes
 * through `desktop:openComfyui` instead of `window.open`.
 */
const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;

/** Whether there is a ComfyUI to open, and a way to open it from here. */
const canOpenComfyui = computed(
  () => Boolean(filterStore.comfyuiUrl) && (!desktop || !!desktop.openComfyui),
);

/**
 * ComfyUI answered that it lacks the ComfyUI-PixlStash node's
 * `open_workflow.js`, so a link would open on whatever it had last. Only a
 * definite no refuses: an unreachable ComfyUI, or a failed ask, leaves the
 * button to try.
 */
const comfyuiLacksNode = ref(false);
let nodeCheck = 0;
watch(
  () => canOpenComfyui.value && filterStore.comfyuiUrl,
  async (url) => {
    const check = ++nodeCheck;
    comfyuiLacksNode.value = false;
    if (!url) return;
    try {
      const { can_open_workflows: canOpen } = await getPixlstashNode();
      if (check === nodeCheck) comfyuiLacksNode.value = canOpen === false;
    } catch (err) {
      console.warn("[workflows] could not ask ComfyUI for the PixlStash node", err);
    }
  },
  { immediate: true },
);
const runDialog = useRunDialogStore();
const tasksStore = useTasksStore();

const route = useRoute();
const router = useRouter();

const tab = ref("workflow");
// A deep link to the Tasks tab, from a notice or a banner (`showTasksTab`).
// This rail is the one a run is usually started from, so it is the one the
// toast's *Show* has to land on.
watch(
  () => sidebarStore.tasksTabRequest,
  () => {
    tab.value = "tasks";
  },
);

// `?tab=recipes`, from the lightbox banner naming the recipe a picture matches
// (#1480). The workflow itself is selected by `?workflow=`, which `WorkflowsView`
// honours; this is the other half, and **it opens the rail too** - landing on
// a selected card with the rail shut is the same dead end the link was for.
//
// Honoured on the value rather than once per visit: the query is a one-shot
// instruction the URL goes on carrying, so a watcher that re-fired on every
// route change would take the tab back off whatever the reader had chosen.
watch(
  () => route.query?.tab,
  (wanted) => {
    if (wanted !== "recipes") return;
    tab.value = "recipes";
    sidebarStore.openWorkflowInspector();
  },
  { immediate: true },
);

// Tasks last, and never anything but last: it is the app's business, not this
// workflow's.
/**
 * Recipes first, as the design bands them; Workflow is still the tab a
 * selection opens on, because it is the one that says what the card IS; and
 * Tasks last, and never anything but last, because it is the app's business
 * and not this workflow's.
 *
 * Recipes answers for a selection of several too — the union of their recipes —
 * so it is never disabled. The Workflow tab is the one with nothing to say
 * about several cards at once.
 */
const tabs = computed(() => [
  { value: "recipes", label: "Recipes", icon: "mdi-bookmark-outline" },
  { value: "workflow", label: "Workflow", icon: "mdi-sitemap-outline" },
  tasksTabFor(tasksStore),
]);

/** The detail of the selected card, and whether its read is still out. */
const detail = ref(null);
const detailPending = ref(false);
const detailFailed = ref(false);
/** One write at a time, named so only the control that fired it shows it. */
const busy = ref("");
const menuOpen = ref(false);
const notesDraft = ref("");

/** Several workflows selected. */
const multiple = computed(() => store.selectedKeys.length > 1);

/** The one workflow id the body reads, or null. Every read and write uses it. */
const selectedKey = computed(() =>
  store.selectedKeys.length === 1 ? store.selectedKeys[0] : null,
);

/**
 * The selected card: the grid's, else the one the detail read brought back
 * (the grid leaves out hidden cards and one-offs, so a selected workflow is
 * not always a listed one).
 */
const card = computed(() => {
  const key = selectedKey.value;
  if (!key) return null;
  const top = store.cards.find((entry) => entry.id === key);
  if (top) return top;
  if (detail.value?.card?.id === key) return detail.value.card;
  return null;
});

/** The shown workflow's last run, when it ended without a picture. */
const runFailure = computed(
  () => (card.value && runDialog.failures[card.value.id]) || null,
);

const runButton = ref(null);
/**
 * Dismiss takes its own button away, so focus goes to Run…: the other thing
 * that clears a failure, and never the page body.
 */
async function dismissRunFailure() {
  runDialog.dismissFailure(card.value.id);
  await nextTick();
  runButton.value?.focus();
}

/**
 * Whether the Recipes list is the body on screen.
 *
 * Not `tab === "recipes"` alone: with nothing selected the empty sentence is
 * the body, and gating the footer on the tab would take Run… off a screen
 * still showing the Workflow body — Run… has to stay on screen and refuse
 * rather than vanish.
 */
const showingRecipes = computed(
  () => tab.value === "recipes" && recipeKeys.value.length > 0,
);

/** Every selected card, because a selection's recipes are their union. */
const recipeKeys = computed(() =>
  store.selectedKeys.length > 1
    ? [...store.selectedKeys]
    : card.value
      ? [card.value.id]
      : [],
);

/** What heads the Recipes tab. */
const recipesName = computed(() =>
  multiple.value
    ? `${store.selectedKeys.length} workflows selected`
    : card.value?.name || "",
);

// Plain numbers, as `WorkflowsView`'s own subtitle writes them: the shelf's
// grouped spelling went with the shelf in F1b, and this screen never used it.
// Split from its prefix so the figure alone is F7's *Show all N pictures*
// link, and the words that place the card stay text.
const picturesLabel = computed(() => outputCount(card.value));

// "Version 3 · 2026-10-06": only where there is a history to speak of, a
// workflow that was pulled from ComfyUI or has been stored more than once.
const userPrefs = useUserPrefsStore();
const versionLine = computed(() => {
  const c = card.value;
  if (!c || !(c.versions > 1 || c.origin_category === "comfyui")) {
    return "";
  }
  const day = formatUserDay(c.version_at, userPrefs.dateFormat);
  // `version`, the number, not `versions`, how many are kept: past the 50
  // kept the two differ.
  const number = c.version;
  return day ? `Version ${number} · ${day}` : `Version ${number}`;
});

// All three read the MODEL SHELF's name where it has one, the same preference
// the card's name row was built from, so the panel and the row do not describe
// one model twice (`modelDisplayName`).
//
// **And all three carry the precision**, because the server has taken it out
// of `name`: without the badge, two quant builds of one model read identically
// here, which is the failure `derive_model_name`'s own docstring names. It is
// appended to the value rather than given a chip of its own - these are
// label/value rows, not a chip row, and `· FP8` is how the shelf's own file
// line already says a second fact about one file.
function withQuant(text, model) {
  const badge = quantBadge(model?.quant);
  // FP8's shared compact label loses E4M3 versus E5M2. Other badges already
  // carry their full identity in their label, and keep this detail row concise.
  const detail = badge?.label === "FP8" ? badge.title : badge?.label;
  return detail ? `${text} · ${detail}` : text;
}

/**
 * The default recipe's models (#1623), from the detail read: the grid sends
 * `default_recipe` null, so until the detail lands the card's own base-card
 * slots answer.
 */
const recipeModels = computed(
  () => detail.value?.card?.default_recipe?.models ?? null,
);

/**
 * A default-recipe model as the rows name it: the base card's slot for the
 * same file where there is one, for its shelf name and precision, else the
 * file without its folders.
 *
 * Matched by the file the slot recorded, never by a derived name: two quant
 * builds derive to one name, and a prefix can name an unrelated slot.
 */
/**
 * A default-recipe model's `kind` is the model-fix kind (`checkpoint`, `vae`,
 * `text_encoder`); a card slot's is the slot word. The slot words each covers.
 */
const RECIPE_KIND_SLOTS = {
  checkpoint: ["checkpoint", "unet"],
  vae: ["vae"],
  text_encoder: ["clip"],
};

function recipeModelLabel(model) {
  // A shelf loader's `filename` is a row id, never shown: `shelf_filename` is
  // its file, and null where the shelf row names none. Every other loader's
  // `shelf_filename` is null too, so the address is what tells them apart.
  const file = String(model.address ?? "").endsWith("/checkpoint_id")
    ? model.shelf_filename
    : model.filename;
  const kinds = RECIPE_KIND_SLOTS[model.kind] ?? [model.kind];
  const slot = (card.value?.models ?? []).find(
    (entry) =>
      kinds.includes(entry.kind) &&
      modelDisplayName(entry) &&
      entry.filename &&
      sameFile(entry.filename, model.filename),
  );
  if (slot) return withQuant(modelDisplayName(slot), slot);
  return file ? fileName(file) : "Unnamed shelf model";
}

// The base models, as the card's own row picks them: a Flux or SD3 graph has
// a `unet` and no `checkpoint`, and reading "checkpoint" alone called that
// missing. The default recipe's picks win once it has been read. Every one,
// once each: a Wan 2.2 graph's high + low pair reads `A + B`, and which loader
// holds which is not claimed (`baseModels`).
const checkpointLabels = computed(() => {
  const recipe = (recipeModels.value ?? [])
    .filter((model) => model.kind === "checkpoint" && model.filename)
    .map(recipeModelLabel);
  const found = recipe.length
    ? recipe
    : baseModels(card.value ?? {}).map((model) =>
        withQuant(modelDisplayName(model), model),
      );
  return [...new Set(found)];
});
const checkpointLabel = computed(() => checkpointLabels.value.join(" + "));
/**
 * How many base models the row prints, whichever source it prints from. The
 * graph's list is one entry per loaded file already, and two shelf ids the
 * shelf no longer holds are two entries reading alike.
 */
const checkpointCount = computed(
  () =>
    checkpointLabels.value.length ||
    (detail.value?.graph_base_models ?? []).length,
);

const checkpointIsUnread = computed(() =>
  card.value ? checkpointUnread(card.value) : false,
);

/** ComfyUI's folders for a base model, as `missing_models` names them. */
const BASE_MODEL_FOLDERS = new Set(["checkpoints", "diffusion_models"]);

/**
 * The support rows a missing file can be replaced in (#1596): the model-fix
 * `slot_kind` and the pre-flight's folder for it. `clip_vision` is not `text_encoders`, so a vision
 * encoder is never offered a text encoder.
 */
const SUPPORT_KINDS = [
  {
    kind: "vae",
    label: "VAE",
    cardKind: "vae",
    folder: "vae",
    noun: "VAE",
    missingText: "VAE missing",
  },
  {
    kind: "text_encoder",
    label: "CLIP",
    cardKind: "clip",
    folder: "text_encoders",
    noun: "text encoder",
    missingText: "Text encoder missing",
  },
];

/** What the pre-flight reports for a name the hub forgot: names no file. */
const FORGOTTEN_MODEL = "(forgotten model)";

/** How long a selection has to settle before ComfyUI is asked about it. */
const PREFLIGHT_SETTLE_MS = 250;

/**
 * The base-model files the run pre-flight says ComfyUI does not have.
 *
 * The card cannot answer this: it names what the recipe recorded, and only
 * ComfyUI's own model list says whether that file is installed. So the tab
 * asks the same question Run… asks, once per card. `preflightAnswered` says
 * whether it has: an unreachable ComfyUI leaves the card's own answer.
 */
const missingBaseFiles = ref([]);
/** The same for each support kind (`SUPPORT_KINDS`), by `slot_kind`. */
const missingFiles = ref({});
const preflightAnswered = ref(false);
let installedCheck = 0;
// A rail that has closed asks nothing: an ask still settling is superseded.
onBeforeUnmount(() => {
  installedCheck += 1;
});

/**
 * The base-model file the graph a run would submit names, for a card that
 * has no name of its own for it (`graph_base_models` on the detail).
 */
const graphBaseModel = computed(
  () => detail.value?.graph_base_models?.[0] || "",
);
/** Every one of them, as the row prints them: `A + B`. */
const graphBaseModels = computed(() =>
  (detail.value?.graph_base_models ?? []).join(" + "),
);

/** The graph was read and names no base model (`[]`, not `null`). */
const graphLoadsNone = computed(
  () =>
    Array.isArray(detail.value?.graph_base_models) &&
    detail.value.graph_base_models.length === 0,
);

/**
 * Whether the base model will not load.
 *
 * Once ComfyUI has answered, its answer: a checkpoint the card cannot name
 * but ComfyUI has is not missing. Until then, and when it cannot be asked, the
 * card's own: a base-model slot with no name. A graph with no such slot (an
 * upscaler) is never missing one.
 */
const checkpointIsMissing = computed(() =>
  preflightAnswered.value
    ? missingBaseFiles.value.length > 0
    : Boolean(card.value && checkpointMissing(card.value)),
);

/** The file that is missing, as recorded (folders included), or "". */
const missingCheckpointFile = computed(
  () =>
    missingBaseFiles.value.find((file) => file !== FORGOTTEN_MODEL) ||
    graphBaseModel.value ||
    checkpointLabel.value,
);

/** The owner's replacement for this card's base model, or null. */
const checkpointFix = computed(
  () =>
    (detail.value?.model_fixes ?? []).find(
      (fix) => fix.slot_kind === "checkpoint",
    ) ?? null,
);

/**
 * Whether the pre-flight says the replacement itself is missing, rather than
 * some other base model of the graph.
 */
const replacementMissing = computed(
  () =>
    Boolean(checkpointFix.value) &&
    missingBaseFiles.value.some((file) =>
      sameFile(file, checkpointFix.value.now),
    ),
);

/**
 * `GET …/model-swap?replacing=` per missing file, keyed `kind:file`: the
 * shelf models that go with the workflow's checkpoint and that the file's
 * loader can load (`replacements`), and why there are none
 * (`replacements_reason`). Read only when the pre-flight says a file is
 * missing.
 */
const replacementsByFile = ref({});

/** Why a missing file has no "Replace with…", as the row says it. */
/** A "Replace with…" picker's options for one missing file, or `[]`. */
function replaceOptionsFor(kind, file) {
  return replacementOptions(
    replacementsByFile.value[`${kind}:${file}`]?.replacements,
  );
}

const replaceOptions = computed(() =>
  missingCheckpointFile.value
    ? replaceOptionsFor("checkpoint", missingCheckpointFile.value)
    : [],
);

/** Whether the picker lists every loadable checkpoint, none known to match. */
const checkpointUnmatched = computed(
  () =>
    replaceOptions.value.length > 0 &&
    replacementsByFile.value[`checkpoint:${missingCheckpointFile.value}`]
      ?.replacements_narrowed === false,
);

/**
 * Why the missing checkpoint has no "Replace with…", or "" while it has one or
 * has not been asked. Every answer without one says so: a missing checkpoint
 * with no picker and no word on why reads as nothing to be done.
 */
const checkpointNoReplacement = computed(() => {
  const answer =
    replacementsByFile.value[`checkpoint:${missingCheckpointFile.value}`];
  if (!answer || answer.replacements?.length) return "";
  const reason = answer.replacements_reason;
  if (reason === "none_loadable")
    return "No checkpoint on your shelf that could replace it is one this loader can load.";
  return ["none_go_with_it", "unread"].includes(reason)
    ? NO_REPLACEMENT_TEXT[reason]
    : "Nothing on your shelf can replace it.";
});

/** Whether two recorded values name one file, whatever their folders. */
function sameFile(a, b) {
  return loraBase(a) === loraBase(b);
}

/**
 * The VAE row, and a CLIP row where the workflow names any, each as entries:
 * a missing file (with its replacement when that is what is missing), a
 * replaced one, or the plain value. The plain values stand only where neither
 * of the others does, as the Checkpoint row's do: the card's names are what
 * the recipes recorded, which after a fix is the original.
 */
const supportRows = computed(() =>
  SUPPORT_KINDS.map((spec) => {
    const fixes = (detail.value?.model_fixes ?? []).filter(
      (fix) => fix.slot_kind === spec.kind,
    );
    const missing = missingFiles.value[spec.kind] ?? [];
    let entries = [
      ...missing.map((file) => ({
        id: `missing:${file}`,
        file,
        fix: fixes.find((fix) => sameFile(fix.now, file)) ?? null,
        // Every original this file replaced: two slots may share one
        // replacement, and the pre-flight names the file, not the slot. The
        // undo is sent by the replacement's name, which the server resolves
        // to all of them.
        originals: fixes
          .filter((fix) => sameFile(fix.now, file))
          .map((fix) => fileName(fix.was))
          .join(" and "),
        options: replaceOptionsFor(spec.kind, file),
        noReplacement:
          NO_REPLACEMENT_TEXT[
            replacementsByFile.value[`${spec.kind}:${file}`]?.replacements_reason
          ] ?? "",
      })),
      ...fixes
        .filter((fix) => !missing.some((file) => sameFile(fix.now, file)))
        .map((fix) => ({ id: `fix:${fix.slot_label}`, fix })),
    ];
    if (!entries.length && recipeModels.value) {
      entries = recipeModels.value
        .filter((model) => model.kind === spec.kind && model.filename)
        .map((model) => ({
          id: `model:${model.address}`,
          text: recipeModelLabel(model),
        }));
    } else if (!entries.length) {
      const models = (card.value?.models ?? []).filter(
        (model) => model.kind === spec.cardKind && modelDisplayName(model),
      );
      entries = models.map((model, index) => ({
        id: `model:${model.slot_label || index}`,
        text: withQuant(modelDisplayName(model), model),
      }));
    }
    if (!entries.length && spec.kind === "vae") {
      entries = [{ id: "vae", text: "From the checkpoint" }];
    }
    return { ...spec, entries };
  }).filter((row) => row.entries.length),
);

/** Replace the missing base model with `now`, or undo that (`null`). */
function replaceCheckpoint(now) {
  const was = now === null ? checkpointFix.value?.was : missingCheckpointFile.value;
  return replaceModel("checkpoint", was, now);
}

/**
 * Replace the missing file `was` in slots of `kind` with the shelf model
 * `now`, or undo the replacement of `was` (`now: null`).
 *
 * The grid is re-read because pictures already made with the replacement
 * join this workflow. The pre-flight is asked again: it is what says
 * whether the model now loads.
 */
function replaceModel(kind, was, now) {
  const key = selectedKey.value;
  if (!key || !was || now === "") return;
  const unmoved = selectionMark();
  return queueWrite("model-fix", async () => {
    try {
      const body = await setWorkflowModelFix(key, {
        was,
        now,
        slot_kind: kind,
      });
      await store.refetch();
      if (!unmoved()) return;
      detail.value = body;
      void checkInstalled(key);
    } catch (err) {
      fail(
        err,
        now === null
          ? "Could not undo that replacement."
          : "Could not replace that model.",
      );
    }
  });
}

/** A recorded model value as a person looks for it: the file, no folders. */
function fileName(value) {
  return String(value).split(/[\\/]/).pop();
}

// ── The LoRA chain (#1478) ─────────────────────────────────────────────────

/** `GET …/lora-chain` for the selected card, and the state of that read. */
const chain = ref(null);
const chainPending = ref(false);
const chainFailed = ref(false);
/** 409: the card has no graph, so there is no chain to read or edit. */
const chainNoGraph = ref(false);

/** The card Edit LoRAs… is open on, or "" when it is shut. */
const editKey = ref("");
const editName = ref("");
const editPictures = ref(0);
const editOrigin = ref("");
/** A LoRA to open with its loader already deleted (Save-as-recipe's hand-over). */
const editDrop = ref("");

/**
 * The loaders as the inspector lists them: the shelf's name, and a strength.
 * A forked chain's lanes are listed after its trunk, so every loader shows.
 */
const chainLoaders = computed(() =>
  [
    ...(chain.value?.loaders ?? []),
    ...(chain.value?.lanes ?? []).flatMap((lane) => lane.loaders ?? []),
  ].map((loader) => {
    return {
      id: String(loader.node_id),
      node_id: String(loader.node_id),
      filename: loader.filename,
      // What the name's tooltip carries, as on a default row.
      file: loader.filename || "",
      sha256: loader.sha256 ? String(loader.sha256).toLowerCase() : "",
      label: loader.on_shelf
        ? loader.name || loraStem(loader.filename)
        : String(loader.filename || loader.name || "").split(/[\\/]/).pop(),
      on_shelf: Boolean(loader.on_shelf),
      strengthText: formatStrength(loader.strength) || "—",
    };
  }),
);

/** "3 of 4 are on your model shelf." */
const shelfLine = computed(() => {
  const total = chainLoaders.value.length;
  const known = chainLoaders.value.filter((loader) => loader.on_shelf).length;
  return `${known} of ${total} ${total === 1 ? "is" : "are"} on your model shelf.`;
});

/**
 * Read the selected card's chain.
 *
 * Its own read rather than a field on the card: the chain is typed from the
 * owner's ComfyUI (`object_info`), which the grid must not wait on. A 409 is a
 * card with no graph, said as such; anything else is "could not read it just
 * now", and Edit LoRAs… stays offered because the dialog reads again.
 *
 * Only the newest read is kept, the way the detail's is: leaving a workflow
 * and coming back asks twice for the same key, and the older answer landing
 * last would put its failure, or its end of waiting, over the newer one.
 */
let chainRead = 0;
async function loadChain(key) {
  const read = ++chainRead;
  chain.value = null;
  chainFailed.value = false;
  chainNoGraph.value = false;
  if (!key) {
    chainPending.value = false;
    return;
  }
  chainPending.value = true;
  try {
    const body = await getLoraChain(key);
    if (read !== chainRead) return;
    chain.value = body;
  } catch (err) {
    if (read !== chainRead) return;
    if (err?.response?.status === 409) {
      chainNoGraph.value = true;
    } else {
      console.warn(`[workflows] could not read the LoRA chain of ${key}`, err);
      chainFailed.value = true;
    }
  } finally {
    if (read === chainRead) chainPending.value = false;
  }
}

// ── The graph's own settings: drop-downs, and Expose a parameter ───────────

/** `GET …/form-inputs` for the selected card: its nodes, and that read's state. */
const formNodes = ref([]);
const formPending = ref(false);
/** Why the nodes could not be read, as the dialog says it; "" when they were. */
const formFailed = ref("");
const exposeOpen = ref(false);

/** What ComfyUI offers for each parameter that is a choice there. */
const formOptions = computed(() => optionsByAddress(formNodes.value));
const formNodeNames = computed(() => nodesByAddress(formNodes.value));

/** The node an exposed row is a setting of; a computed row needs no telling. */
function nodeOf(row) {
  return row.exposed ? formNodeNames.value[parameterAddress(row)] || "" : "";
}

/**
 * The nodes with a setting that is not a parameter yet, and only those
 * settings. Told by the rows on screen rather than the read's own `exposed`,
 * so a row added or removed since is counted without asking again.
 */
const exposable = computed(() => {
  const rows = new Set(defaults.value.map(parameterAddress));
  return formNodes.value
    .map((node) => ({
      ...node,
      inputs: node.inputs.filter((input) => !rows.has(parameterAddress(input))),
    }))
    .filter((node) => node.inputs.length);
});

/**
 * Read the selected card's settable inputs. Its own read for the LoRA
 * chain's reason: it is typed from the owner's ComfyUI. A failure leaves the
 * rows as plain boxes, which still set the value. Only the newest read is
 * kept, as with the chain.
 */
let formRead = 0;
async function loadFormInputs(key) {
  const read = ++formRead;
  formNodes.value = [];
  formFailed.value = "";
  if (!key) {
    formPending.value = false;
    return;
  }
  formPending.value = true;
  try {
    const body = await getWorkflowFormInputs(key);
    if (read !== formRead) return;
    formNodes.value = body.nodes ?? [];
  } catch (err) {
    if (read !== formRead) return;
    console.warn(`[workflows] could not read the form inputs of ${key}`, err);
    formFailed.value =
      err?.response?.status === 409
        ? "PixlStash has no graph for this workflow, so it has no nodes to pick from."
        : errorMessage(err, "Could not read this workflow's nodes just now.");
  } finally {
    if (read === formRead) formPending.value = false;
  }
}

// ── The workflow's LoRAs: shared, and the pile ─────────────────────────────

/** `GET …/lora-summary` for the selected workflow, and its read state. */
const summary = ref(null);
const summaryFailed = ref(false);

/** The cover picture, which decides the top of the pile. */
const coverPictureId = computed(
  () => card.value?.covers?.[0]?.picture_id ?? null,
);

/** Which read is the latest, so an older answer never overwrites a newer one. */
let summaryRead = 0;

/**
 * Read the workflow's LoRAs for `key`.
 *
 * A re-read of the SAME workflow (when the cover arrives) keeps the pile on
 * screen until the answer lands: blanking it unmounts the open fan and drops
 * focus to the page.
 */
let summaryKey = null;
async function loadSummary(key) {
  const read = ++summaryRead;
  if (summaryKey !== key) summary.value = null;
  summaryKey = key;
  summaryFailed.value = false;
  if (!key) {
    summary.value = null;
    return;
  }
  try {
    const body = await getLoraSummary(key, {
      cover: coverPictureId.value ?? undefined,
    });
    if (read !== summaryRead) return;
    summary.value = body;
  } catch (err) {
    if (read !== summaryRead) return;
    console.warn(`[workflows] could not read the LoRAs of ${key}`, err);
    summary.value = null;
    summaryFailed.value = true;
  }
}

/** A digest as both reads spell it: `asset:<sha256>` or bare, lowercased. */
function digestOf(value) {
  return String(value || "")
    .replace(/^asset:/i, "")
    .toLowerCase();
}

/** The default recipe itself, from the detail read, or null. */
const defaultRecipe = computed(
  () => detail.value?.card?.default_recipe ?? null,
);

/** The workflow's checkpoint and LoRA values with their picture counts. */
const recipeValues = computed(
  () =>
    card.value?.recipe_values ?? detail.value?.card?.recipe_values ?? null,
);

/**
 * A `recipe_values` entry worth a *Show N*: one below the workflow's own
 * count (in every picture it only repeats the head's link) and above none.
 */
function showable(value) {
  const count = Number(value?.pictures) || 0;
  const total = Number(card.value?.picture_count) || 0;
  return count > 0 && count < total ? value : null;
}

/** The `recipe_values` entry for a recorded file, whatever its folders. */
function valueFor(values, file) {
  if (!file) return null;
  return (values ?? []).find((value) => sameFile(value.name, file)) ?? null;
}

/**
 * Pictures per star, 1★ first, for the head's histogram; null when none of
 * its pictures is rated, which draws no chart at all.
 */
const ratingCounts = computed(() => {
  const counts = card.value?.rating_counts;
  return Array.isArray(counts) && counts.some(Boolean) ? counts : null;
});

/** The default recipe's checkpoints: a Wan 2.2 pair is two. */
const recipeCheckpoints = computed(() =>
  (recipeModels.value ?? []).filter(
    (model) => model.kind === "checkpoint" && model.filename,
  ),
);

/** Whether the owner set any of them: overrides are per address. */
const checkpointEdited = computed(() =>
  recipeCheckpoints.value.some((model) => model.provenance === "edited"),
);

/** The checkpoint row's *Show N*, one per recipe checkpoint that has one. */
const checkpointShows = computed(() =>
  recipeCheckpoints.value
    .map((model) => {
      const value = showable(
        valueFor(recipeValues.value?.checkpoints, model.filename),
      );
      return value && { ...value, label: recipeModelLabel(model) };
    })
    .filter(Boolean),
);

/** Whether the Checkpoint row's "+K others" is open. */
const othersOpen = ref(false);

/** The other checkpoints its pictures used, each with its own *Show N*. */
const otherCheckpoints = computed(() => {
  const files = recipeCheckpoints.value.map((model) => model.filename);
  if (!files.length) return [];
  return (recipeValues.value?.checkpoints ?? [])
    .filter(
      (value) =>
        !files.some((file) => sameFile(value.name, file)) && showable(value),
    )
    .map((value) => ({
      name: value.name,
      pictures: value.pictures,
      label: modelLabel(fileName(value.name)),
    }));
});

/**
 * No sample to take a default from (a workflow with no pictures), so the
 * LoRAs a run loads are the graph's own chain, and the rows are that.
 */
const usingChain = computed(
  () =>
    Boolean(detail.value) &&
    !defaultRecipe.value?.sampled &&
    !(defaultRecipe.value?.loras ?? []).length,
);

/** The summary's LoRAs by one of their fields, and whether each is everywhere. */
function summaryBy(field) {
  const uses = new Map();
  for (const [list, everywhere] of [
    [summary.value?.shared, true],
    [summary.value?.varying, false],
  ]) {
    for (const use of list ?? []) {
      if (use[field]) uses.set(digestOf(use[field]), { use, everywhere });
    }
  }
  return uses;
}

/** By the shelf digest they resolve to, where they do. */
const summaryByDigest = computed(() => summaryBy("sha256"));

/** By their `asset:` reference: a hash of the file's NAME, as graphs name it. */
const summaryUses = computed(() => summaryBy("asset"));

/** The asset references of the default recipe's LoRAs, which ALSO USED leaves out. */
const defaultAssets = computed(
  () =>
    new Set(
      (defaultRecipe.value?.loras ?? [])
        .map((lora) => digestOf(lora.asset))
        .filter(Boolean),
    ),
);

/**
 * The default recipe's LoRAs as rows, in the chain's order where the chain
 * names them.
 *
 * Joined to the summary by the `asset:` reference both name a LoRA by (the
 * shelf digest is NOT that: it hashes the file's content, the reference its
 * name), and to the chain by the shelf digest, which is what the chain has.
 * Never by bare filename: a forgotten name has none. A default the server
 * could not give an asset renders without a count rather than with a guess.
 */
const defaultLoras = computed(() => {
  const loras = defaultRecipe.value?.loras ?? [];
  const byDigest = new Map(
    chainLoaders.value
      .filter((loader) => loader.sha256)
      .map((loader) => [loader.sha256, loader]),
  );
  const total = Number(summary.value?.pictures) || 0;
  const rows = loras.map((lora, index) => {
    const digest = digestOf(lora.sha256);
    const asset = digestOf(lora.asset);
    const found =
      (asset ? summaryUses.value.get(asset) : null) ??
      (digest ? summaryByDigest.value.get(digest) : null);
    const loader = digest ? byDigest.get(digest) : null;
    const edited = lora.provenance === "edited";
    let coverage = "";
    if (total && found && !found.everywhere && found.use.pictures) {
      coverage = `in ${found.use.pictures} of ${total}`;
    } else if (total && (asset || digest) && !found && edited) {
      // In the default by the owner's edit alone.
      coverage = "not in any picture yet";
    }
    return {
      id: asset || digest || lora.filename || `lora-${index}`,
      file: lora.filename || "",
      label:
        found?.use.name ||
        loader?.label ||
        (lora.filename ? loraStem(lora.filename) : "") ||
        "A LoRA whose name was forgotten",
      on_shelf: found ? Boolean(found.use.on_shelf) : (loader?.on_shelf ?? true),
      strengthText: formatStrength(lora.strength),
      edited,
      coverage,
      show:
        coverage === "not in any picture yet"
          ? null
          : showable(valueFor(recipeValues.value?.loras, lora.filename)),
      order: loader ? chainLoaders.value.indexOf(loader) : Infinity,
    };
  });
  return rows.sort((a, b) => a.order - b.order);
});

/** The LoRA rows on screen: the default recipe's, or the chain's (above). */
const loraRows = computed(() =>
  usingChain.value ? chainLoaders.value : defaultLoras.value,
);

/** The line under the LoRA rows: the chain's order, or who is off the shelf. */
const loraNote = computed(() => {
  if (usingChain.value) {
    return chainLoaders.value.length
      ? `In the order the chain applies them. ${shelfLine.value}`
      : "";
  }
  const missing = defaultLoras.value.filter((lora) => !lora.on_shelf);
  if (!missing.length) return "";
  const names = missing.map((lora) => lora.label).join(", ");
  return `${names} ${missing.length === 1 ? "is" : "are"} not on your shelf.`;
});

/**
 * The base graph's optional stages and whether the default runs each.
 * `detail` is which kind a stage is, where it can be several (an upscale).
 */
const stageRows = computed(() =>
  Object.entries(defaultRecipe.value?.stages ?? {}).map(([key, on]) => ({
    key,
    on: Boolean(on),
    detail: defaultRecipe.value?.stage_details?.[key] || "",
    label:
      STAGE_LABELS[key] ??
      key.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase()),
  })),
);

/** ALSO USED: every LoRA the pictures used, less the default's. */
const alsoUsed = computed(() => {
  // A default with no asset reference cannot be joined by one, so it is
  // matched by its file instead, or it shows twice. Only those: a default
  // WITH one never matches by name, since two folders can hold one name.
  const unjoined = (defaultRecipe.value?.loras ?? []).filter(
    (lora) => !digestOf(lora.asset) && lora.filename,
  );
  const shelfFiles = new Set(
    (defaultRecipe.value?.loras ?? [])
      .map((lora) => digestOf(lora.sha256))
      .filter(Boolean),
  );
  // `shared` too: a LoRA in every picture is not in the default when it is a
  // person's (the workflow runs without) or the owner took it out, and it
  // would otherwise be listed nowhere and could not be added back.
  return [
    ...(summary.value?.shared ?? []),
    ...(summary.value?.varying ?? []),
  ].filter(
    (use) =>
      !defaultAssets.value.has(digestOf(use.asset)) &&
      !(use.sha256 && shelfFiles.has(digestOf(use.sha256))) &&
      !(
        use.filename &&
        unjoined.some((lora) => sameFile(lora.filename, use.filename))
      ),
  );
});

/**
 * The summary the pile draws: its `varying` is ALSO USED. `without` ("No
 * LoRA") is counted by the server against the whole of `varying`, so once a
 * default LoRA is taken out of the pile it answers a different question, and
 * is dropped rather than shown with a wrong count.
 */
const pileSummary = computed(() => ({
  ...summary.value,
  varying: alsoUsed.value,
  without:
    alsoUsed.value.length === (summary.value?.varying?.length ?? 0) &&
    alsoUsed.value.every((use) => summary.value?.varying?.includes(use))
      ? (summary.value?.without ?? null)
      : null,
  cover_asset: alsoUsed.value.some(
    (use) => use.asset === summary.value?.cover_asset,
  )
    ? summary.value.cover_asset
    : null,
}));

const pileNote = computed(() => {
  const rest = alsoUsed.value.length - 1;
  const top = pileSummary.value.cover_asset
    ? "On top: the cover picture's LoRA."
    : "On top: the LoRA most pictures used.";
  if (!rest) return `${top} Open it to see its pictures.`;
  return `${top} Open the pile to see the other ${rest === 1 ? "one" : rest}.`;
});

/** A *Show N*'s accessible name, as the pile's own says it. */
function showLabel(count, value) {
  return `Show the ${pictureCount(count)} in ${
    card.value?.name || "this workflow"
  } made with ${value}`;
}

/** *Show N* on a default-recipe row: the grid, on workflow + that value. */
function showValue(kind, value, label) {
  showValuePictures({
    id: selectedKey.value,
    name: card.value?.name || "this workflow",
    kind,
    value,
    label,
  });
}

/** *Show N* on the pile: the grid, narrowed to this workflow's pictures of one LoRA. */
function showLora(row) {
  showLoraPictures({
    id: selectedKey.value,
    lora: row.asset,
    name: card.value?.name || "this workflow",
    loraName: row.name || "A forgotten LoRA",
  });
}

/** Open Edit LoRAs… on `key`, with `drop` already struck through if given. */
function openEditLoras(key, drop = "") {
  if (!key) return;
  const shown = card.value?.id === key ? card.value : null;
  editName.value = shown?.name || "";
  editPictures.value = Number(shown?.picture_count) || 0;
  editOrigin.value = shown?.origin_category || "";
  editDrop.value = drop;
  editKey.value = key;
}

/**
 * Edit LoRAs… saved over the card itself. The selection did not move, so the
 * watcher below reads nothing: the chain and the detail are asked again here.
 */
function onOverwritten(key) {
  if (selectedKey.value !== key) return;
  void loadDetail(key);
  void loadChain(key);
  void loadFormInputs(key);
}

function closeEditLoras() {
  editKey.value = "";
  editDrop.value = "";
}

// The name and count arrive with the card when the dialog was opened from a
// link before the grid or the detail read had landed.
watch(card, (next) => {
  if (!editKey.value || next?.id !== editKey.value) return;
  if (!editName.value) editName.value = next.name || "";
  if (!editPictures.value) editPictures.value = Number(next.picture_count) || 0;
  if (!editOrigin.value) editOrigin.value = next.origin_category || "";
});

// `?workflow=<id>&edit=loras&drop_lora=<file>`, from Save-as-recipe's "The
// workflow" (#1478): select that workflow, open the rail on its Workflow tab,
// and open Edit LoRAs… with that entry already deleted.
//
// `edit` and `drop_lora` are one-shot: they are taken back off the URL once
// honoured, so a reload or a Back does not reopen a dialog the owner has since
// cancelled. `workflow` stays and is honoured once per value. It lands on the
// Workflow tab unless `?tab=recipes` asks for Recipes (a saved recipe's link):
// this runs after the `?tab=` watcher above and would otherwise undo it.
let honouredCard = null;

watch(
  () => [route.query?.workflow, route.query?.edit, route.query?.drop_lora],
  ([wanted, edit, drop]) => {
    if (typeof wanted !== "string" || !wanted) {
      honouredCard = null;
      return;
    }
    if (honouredCard !== wanted || edit) {
      honouredCard = wanted;
      store.select(wanted);
      tab.value = route.query?.tab === "recipes" && !edit ? "recipes" : "workflow";
      sidebarStore.openWorkflowInspector();
    }
    if (edit === EDIT_LORAS) {
      openEditLoras(wanted, typeof drop === "string" ? drop : "");
    }
    if (edit !== undefined || drop !== undefined) {
      const rest = Object.fromEntries(
        Object.entries(route.query || {}).filter(
          ([name]) => name !== "edit" && name !== "drop_lora",
        ),
      );
      void router?.replace?.({ query: rest });
    }
  },
  { immediate: true },
);

/** Every slot the card names, for the ComfyUI-users disclosure. */
const nodeNames = computed(() =>
  [...(card.value?.models ?? []), ...(card.value?.loras ?? [])].map(
    (slot, index) => ({
      id: slot.slot_label || `slot-${index}`,
      kind: slot.kind,
      label: slot.slot_label,
    }),
  ),
);

/**
 * The workflow's defaults, each with whether it is set each run (`pinned`,
 * the stored pin list's shape): the default recipe's featured values (#1623).
 *
 * The detail read carries the pins; a card with none (`null`, not `[]`) gets
 * the default set, which is what the server means by "the default pins apply
 * again".
 */
const defaults = computed(() => {
  const rows = detail.value?.card?.default_recipe?.values ?? [];
  return rows.map((row) => ({
    ...row,
    pinned: setEachRun(row, detail.value?.pins),
  }));
});

/**
 * The pins as they were when this workflow was opened. The panel groups by
 * these, not by the live pins, so a row toggled here keeps its place under
 * the pointer and moves to its new group the next time the workflow opens.
 */
const openedPins = ref(null);

// The Run popup freed a parameter on this workflow: take its list, or the next
// lock here would write the whole set from a stale copy. Taking it also
// retires any read still out, which may have been answered before the write
// landed; with no detail yet, the read is asked again instead.
watch(
  () => runDialog.pinsWritten,
  (written) => {
    if (!written || !stillOn(written.workflowId)) return;
    if (detail.value) {
      detailRead += 1;
      detailPending.value = false;
      detail.value = { ...detail.value, pins: written.pins };
    } else {
      loadDetail(written.workflowId);
    }
  },
);

/**
 * An imported workflow with no recipe, shown only when it has no defaults
 * either: an editor-format file nothing has converted yet (#1530). The server
 * converts one on the first read that needs its graph, with ComfyUI's
 * `object_info`, and stores it, so this is what is left when ComfyUI could not
 * be asked or could not run it.
 */
const editorOnly = computed(
  () => Boolean(card.value?.imported) && card.value?.variant_count === 0,
);

const runDefaults = computed(() =>
  defaults.value.filter((row) => setEachRun(row, openedPins.value)),
);
const fixedDefaults = computed(() =>
  defaults.value.filter((row) => !setEachRun(row, openedPins.value)),
);

/**
 * The Recipes tab wrote this workflow's defaults ("Make these the defaults").
 * Take its answer, or the next reset, pin or edit here would PUT the whole set
 * from a stale copy and drop what it wrote.
 */
function takeDefaults(workflowId, body) {
  if (workflowId === selectedKey.value && body) {
    // Newer than any read still out: that read's answer is the set before.
    detailRead += 1;
    detail.value = body;
  }
}

/** Which detail read is the latest; a write's answer also counts as one. */
let detailRead = 0;

/** Read one card's detail, and say which of the three states it is in. */
async function loadDetail(key) {
  const read = ++detailRead;
  detail.value = null;
  detailFailed.value = false;
  if (!key) {
    detailPending.value = false;
    return;
  }
  detailPending.value = true;
  try {
    const body = await getWorkflowCard(key);
    // Another card may have been selected while this was on the wire, or a
    // newer answer (the Recipes tab's write) already landed.
    if (selectedKey.value !== key || read !== detailRead) return;
    detail.value = body;
    openedPins.value = body.pins ?? null;
    notesDraft.value = body.notes ?? "";
  } catch (err) {
    console.warn(`[workflows] could not read the card ${key}`, err);
    if (selectedKey.value !== key) return;
    detailFailed.value = true;
  } finally {
    if (selectedKey.value === key) detailPending.value = false;
  }
}

function fail(err, fallback) {
  console.warn("[workflows] a workflow write failed", err);
  notices.push({ level: "error", text: errorMessage(err, fallback) });
}

/**
 * Run a write after whichever is already out, never beside it.
 *
 * `defaults`, `pins`, the notes and the card all come back on one `detail`,
 * so two writes in flight means the slower answer discards the faster one's
 * change. **Queued rather than refused**, because the gesture that starts
 * the second write is routinely the one that ends the first: clicking a pin
 * is what blurs the notes box, so a refusal would drop that click and leave
 * the pin looking dead.
 *
 * The chain is never broken by a failure — each link catches its own — and
 * `busy` names only the link that is running, so the control that fired it
 * is the one that shows it.
 */
let writes = Promise.resolve();

/**
 * Whether a default-recipe LoRA is a pile row's: the same `asset` reference,
 * or the same shelf file by digest (one file loaded under two names is one
 * LoRA, and the default names it under only one of them).
 */
function sameLora(lora, use) {
  if (digestOf(lora.asset) && digestOf(lora.asset) === digestOf(use.asset)) {
    return true;
  }
  return Boolean(
    digestOf(lora.sha256) && digestOf(lora.sha256) === digestOf(use.sha256),
  );
}

/** The LoRAs heading: where focus goes when the last pile row leaves. */
const defaultHeading = ref(null);

/**
 * *Add to default* on a pile row (#1653): the LoRA joins the default recipe,
 * at the strength its pictures used most, and leaves the pile. Undo drops
 * the edit, but only while it is still the one this made: a strength set
 * since, or the LoRA already taken out again, is somebody else's to keep.
 */
function addLoraToDefault(row) {
  const key = selectedKey.value;
  const name = row.name || "That LoRA";
  return queueWrite(`default-lora:${row.asset}`, async () => {
    let body;
    try {
      body = await setWorkflowDefaultLora(key, { asset: row.asset, include: true });
    } catch (err) {
      fail(err, `Could not add ${name} to the default recipe.`);
      return;
    }
    // Written either way: the notice (and its Undo) is owed even when the
    // selection has moved on while the answer was out.
    const added = (body?.card?.default_recipe?.loras ?? []).find((lora) =>
      sameLora(lora, row),
    );
    if (stillOn(key)) {
      detail.value = body;
      if (!alsoUsed.value.length) {
        await nextTick();
        defaultHeading.value?.focus();
      }
    }
    notices.push({
      level: "success",
      timeout: 8000,
      text: `${name} is now in the default recipe. Your pictures stay where they are.`,
      action: {
        label: "Undo",
        handler: () =>
          queueWrite(`default-lora:${row.asset}`, async () => {
            try {
              const now = await getWorkflowCard(key);
              const still = (now?.card?.default_recipe?.loras ?? []).find(
                (lora) => sameLora(lora, row),
              );
              if (
                !still ||
                still.provenance !== "edited" ||
                still.strength !== added?.strength
              ) {
                notices.push({
                  level: "info",
                  text: `Nothing to undo: ${name} has been changed since.`,
                });
                return;
              }
              // By the edit's own digest, which still names it if the file
              // has left the shelf since.
              const undone = await setWorkflowDefaultLora(key, {
                sha256: still.sha256,
                include: null,
              });
              if (stillOn(key)) detail.value = undone;
            } catch (err) {
              fail(err, `Could not take ${name} back out of the default recipe.`);
            }
          }),
      },
    });
  });
}

function queueWrite(token, work) {
  writes = writes.then(async () => {
    busy.value = token;
    try {
      await work();
    } finally {
      busy.value = "";
    }
  });
  return writes;
}

/**
 * Whether a write that has come back still belongs on screen.
 *
 * Every handler awaits, and the selection can move while it does — clicking
 * another card is exactly what blurs the notes box and fires its save. An
 * answer for the card that has gone must not be written into the rail
 * showing the one that arrived.
 */
function stillOn(key) {
  return selectedKey.value === key;
}

/**
 * A mark over the selection itself, for the writes that re-read the grid: by
 * identity, since every gesture that selects writes a new array, so a reader
 * who moved on meanwhile — even back to the same workflow — is told apart.
 */
function selectionMark() {
  const keys = toRaw(store.selectedKeys);
  return () => toRaw(store.selectedKeys) === keys;
}

/**
 * The defaults a whole-set write for `key` may be built from, or null.
 *
 * Read when the queued write RUNS, not when it was queued, so an earlier write
 * it waited behind is in them. By then the rail may have moved on: `defaults`
 * is then another card's (or nothing, mid-read), and a whole-set PUT built
 * from them would replace `key`'s own overrides. Refused and said instead.
 */
function defaultsFor(key) {
  if (stillOn(key) && detail.value) return defaults.value;
  console.warn(`[workflows] a write to ${key} dropped: the selection moved`);
  notices.push({
    level: "error",
    text: "That change was not saved: the workflow changed before it could be.",
  });
  return null;
}

/**
 * The edited defaults except one address, as a whole-set write sends them.
 *
 * Every whole-set writer starts here: the route replaces the set, so what is
 * not sent is cleared.
 */
function editedExcept(rows, slotLabel, inputName) {
  return rows
    .filter(
      (entry) =>
        entry.provenance === "edited" &&
        !(entry.slot_label === slotLabel && entry.input_name === inputName),
    )
    .map((entry) => ({
      slot_label: entry.slot_label,
      input_name: entry.input_name,
      value: entry.value,
    }));
}

/**
 * Set one value as the workflow's own ("Yours") when `edit` is `{value}`, or
 * put it back to what the pictures say when there is no `edit`.
 *
 * The route replaces the whole override set, so this sends every other edited
 * value back untouched; sending only the survivors of a filter is the same
 * request and is what makes "reset one" possible at all. Locking afterwards
 * keeps a set value, since a fixed parameter runs the workflow's value.
 */
function writeDefault(row, edit = null) {
  const key = selectedKey.value;
  if (!key) return;
  return queueWrite(`default:${row.label}`, async () => {
    const rows = defaultsFor(key);
    if (!rows) return;
    try {
      const kept = editedExcept(rows, row.slot_label, row.input_name);
      if (edit) {
        kept.push({
          slot_label: row.slot_label,
          input_name: row.input_name,
          value: edit.value,
        });
      }
      const body = await setWorkflowDefaults(key, kept);
      if (stillOn(key)) detail.value = body;
    } catch (err) {
      fail(
        err,
        edit ? `Could not set ${row.label}.` : "Could not reset that value.",
      );
    }
  });
}

/**
 * Expose a parameter: `entry` (`{slot_label, input_name, value}`, the dialog's
 * pick) becomes one of the workflow's own values, set each run.
 *
 * Two whole-set writes, the default and then its pin. If the pin fails the row
 * is still there, fixed, and its lock frees it.
 */
function exposeParameter(entry) {
  const key = selectedKey.value;
  exposeOpen.value = false;
  if (!key) return;
  return queueWrite("expose", async () => {
    const rows = defaultsFor(key);
    if (!rows) return;
    const address = { slot_label: entry.slot_label, input_name: entry.input_name };
    const others = (list) =>
      list
        .filter(
          (row) =>
            row.slot_label !== entry.slot_label ||
            row.input_name !== entry.input_name,
        )
        .map((row) => ({
          slot_label: row.slot_label,
          input_name: row.input_name,
        }));
    try {
      const body = await setWorkflowDefaults(key, [
        ...editedExcept(rows, entry.slot_label, entry.input_name),
        { ...address, value: entry.value },
      ]);
      if (stillOn(key)) detail.value = body;
      const stored = body.pins;
      const pins = [
        ...others(Array.isArray(stored) ? stored : rows.filter((row) => row.pinned)),
        address,
      ];
      const pinned = await setWorkflowPins(key, pins);
      if (!stillOn(key)) return;
      detail.value = { ...detail.value, pins: pinned.pins ?? pins };
      // Under "Set each run" at once: it was not there when the workflow was
      // opened, so the opened grouping has no place for it.
      openedPins.value = [
        ...others(rows.filter((row) => setEachRun(row, openedPins.value))),
        address,
      ];
    } catch (err) {
      fail(err, `Could not expose ${entry.input_name}.`);
    }
  });
}

/** Pin or unpin one parameter. Whole-set, like the defaults. */
function togglePin(row) {
  const key = selectedKey.value;
  if (!key) return;
  return queueWrite(`default:${row.label}`, async () => {
    const rows = defaultsFor(key);
    if (!rows) return;
    try {
      // From the stored list when there is one, so a pin whose parameter is
      // not among today's defaults survives: it decides a Run field now.
      const stored = detail.value?.pins;
      const base = Array.isArray(stored)
        ? stored
        : rows.filter((entry) => entry.pinned);
      const same = (entry) =>
        entry.slot_label === row.slot_label &&
        entry.input_name === row.input_name;
      // Read now, not at click time: a write queued ahead may have moved it.
      const pinned = rows.find(same)?.pinned ?? row.pinned;
      const pins = (
        pinned
          ? base.filter((entry) => !same(entry))
          : [...base.filter((entry) => !same(entry)), row]
      ).map((entry) => ({
        slot_label: entry.slot_label,
        input_name: entry.input_name,
      }));
      const body = await setWorkflowPins(key, pins);
      if (stillOn(key)) {
        detail.value = { ...detail.value, pins: body.pins ?? pins };
      }
    } catch (err) {
      fail(err, "Could not change that pin.");
    }
  });
}

/**
 * Save the notes, on blur.
 *
 * Queued like every other write, and it has to be: this PATCH answers with
 * the whole detail, pins included, so landing beside a pin write puts the
 * pre-toggle pins back on screen while the server holds the new ones. Blur
 * is exactly when that happens — clicking the pin is what blurs this box.
 *
 * The draft is compared at QUEUE time, not at run time: the comparison is
 * "did the person change anything", and by the time an earlier write has
 * finished `detail` may already carry what they typed.
 */
function saveNotes() {
  const key = selectedKey.value;
  // `detail` null is a card whose notes have not arrived; the box is not
  // drawn then, and the draft belongs to whatever was read last.
  if (
    !key ||
    !detail.value ||
    notesDraft.value === (detail.value.notes ?? "")
  ) {
    return;
  }
  const notes = notesDraft.value || null;
  return queueWrite("notes", async () => {
    try {
      const body = await patchWorkflowCard(key, { notes });
      if (stillOn(key)) detail.value = body;
    } catch (err) {
      fail(err, "Could not save those notes.");
    }
  });
}

function toggleHidden() {
  const key = selectedKey.value;
  if (!key || !detail.value) return;
  menuOpen.value = false;
  const hiding = !detail.value.hidden;
  const unmoved = selectionMark();
  return queueWrite("hidden", async () => {
    try {
      const body = await patchWorkflowCard(key, { hidden: hiding });
      if (stillOn(key)) detail.value = body;
      // A hidden card leaves the grid, so the rail would have nothing to
      // draw were it not for the detail fallback in `card` — which is also
      // what keeps Unhide reachable from here.
      await store.refetch();
      if (unmoved() && selectedKey.value !== key) store.select(key);
    } catch (err) {
      fail(
        err,
        hiding ? "Could not hide that workflow." : "Could not unhide it.",
      );
    }
  });
}

/** What Run… runs: the card on screen. Nothing while several are selected. */
const runTarget = computed(() => (multiple.value ? null : card.value));

/** Why Run… refuses, when it is a reason worth a sentence. */
const runDescribedBy = computed(() =>
  multiple.value ? "wftab-run-reason" : undefined,
);

/** Why Open a copy in ComfyUI refuses, when it does. */
const openDescribedBy = computed(() => {
  if (multiple.value) return "wftab-open-reason";
  return comfyuiLacksNode.value ? "wftab-open-node-reason" : undefined;
});

/**
 * Run… opens the Run popup on THIS card (v1.12 F5).
 *
 * No picture behind it, so the popup shows the card's cover, an empty prompt
 * and a set picker for where the output is filed - which is the one thing a
 * card-sourced run has to be told and a picture-sourced one already knows.
 *
 * Refused outright while several cards are selected: the button stays on
 * screen and `aria-disabled` says why, rather than disappearing and leaving
 * nothing to explain.
 */
function run() {
  const target = runTarget.value;
  if (!target) return;
  runDialog.openRun({
    kind: "card",
    workflowId: target.id,
    name: target.name,
    // Through the helper: a raw `covers` entry is API-relative and an
    // `<img src>` resolves it against the page origin instead.
    coverUrl: target.covers?.[0] ? workflowCoverUrl(target.covers[0]) : "",
    emptyPrompt: true,
  });
}

/**
 * Open ComfyUI on what Run… runs (`runTarget`), in a new tab.
 *
 * ComfyUI takes no workflow from a URL, so the id rides along as
 * `?pixlstash_workflow=` and the ComfyUI-PixlStash node fetches the graph
 * (`GET /workflows/{id}/graph`) and loads it. Synchronous on purpose: a
 * `window.open` after an await is what popup blockers refuse.
 */
function openInComfyui() {
  const target = runTarget.value;
  if (!target || !filterStore.comfyuiUrl || comfyuiLacksNode.value) return;
  let url;
  try {
    url = new URL(filterStore.comfyuiUrl);
  } catch (err) {
    console.warn(`[workflows] bad ComfyUI address ${filterStore.comfyuiUrl}`, err);
    url = null;
  }
  if (!url || !/^https?:$/.test(url.protocol)) {
    notices.push({
      level: "error",
      text: "The ComfyUI address in Settings is not a web address.",
    });
    return;
  }
  // `0.0.0.0` is ComfyUI listening everywhere: fine for the server, and a
  // browser cannot navigate to it. The machine this page came from is the one.
  if (["0.0.0.0", "[::]"].includes(url.hostname)) {
    url.hostname = window.location.hostname;
  }
  url.searchParams.set("pixlstash_workflow", target.id);
  if (desktop?.openComfyui) {
    desktop
      .openComfyui(url.toString())
      .then((opened) => {
        if (!opened) throw new Error("the shell refused the link");
      })
      .catch((err) => {
        console.warn("[workflows] the desktop shell would not open ComfyUI", err);
        notices.push({ level: "error", text: "Could not open ComfyUI." });
      });
    return;
  }
  window.open(url.toString(), "_blank", "noopener,noreferrer");
}

watch(
  selectedKey,
  (key) => {
    othersOpen.value = false;
    exposeOpen.value = false;
    void loadDetail(key);
    void loadChain(key);
    void loadFormInputs(key);
  },
  { immediate: true },
);

// The pile's top is the cover's LoRA, so the summary is asked again when the
// cover arrives after the key does.
watch(
  () => [selectedKey.value, coverPictureId.value],
  ([key]) => {
    void loadSummary(key);
  },
  { immediate: true },
);

/**
 * Ask the run pre-flight whether the base model loads, once per card./**
 * Ask the run pre-flight whether the base model loads, once per card.
 *
 * Keyed on the card alone: every write answers with a new `detail` too, and a
 * pin toggle is no reason to ask ComfyUI again. Each ask is a fresh
 * `object_info` read, so it waits for the selection to settle - arrowing across
 * the grid asks once, not per card - and a superseded answer is dropped. A
 * failed or unanswerable check leaves the card's own answer standing.
 */
watch(
  () => (detail.value ? selectedKey.value : null),
  (key) => checkInstalled(key),
  { immediate: true },
);

async function checkInstalled(key) {
  const check = ++installedCheck;
  missingBaseFiles.value = [];
  missingFiles.value = {};
  preflightAnswered.value = false;
  replacementsByFile.value = {};
  if (!key) return;
  await new Promise((resolve) => setTimeout(resolve, PREFLIGHT_SETTLE_MS));
  if (check !== installedCheck) return;
  try {
    const answer = await preflightWorkflowRun({
      workflow_id: key,
      values: [],
    });
    if (check !== installedCheck || !stillOn(key)) return;
    preflightAnswered.value = true;
    const missing = (answer?.groups ?? [])
      .flatMap((group) => group.reasons ?? [])
      .filter((reason) => reason.code === "missing_models")
      .flatMap((reason) => reason.models ?? []);
    const filesIn = (folders) => [
      ...new Set(
        missing
          .filter((model) => folders.has(model.folder))
          .map((model) => String(model.file)),
      ),
    ];
    missingBaseFiles.value = filesIn(BASE_MODEL_FOLDERS);
    // A forgotten name is no file anybody could replace.
    missingFiles.value = Object.fromEntries(
      SUPPORT_KINDS.map((spec) => [
        spec.kind,
        filesIn(new Set([spec.folder])).filter(
          (file) => file !== FORGOTTEN_MODEL,
        ),
      ]),
    );
  } catch (err) {
    console.warn(`[workflows] could not pre-flight ${key}`, err);
    return;
  }
  // Only a file ComfyUI named can be replaced: a forgotten name is no file.
  const asks = [
    ...(missingCheckpointFile.value &&
    missingCheckpointFile.value !== FORGOTTEN_MODEL &&
    missingBaseFiles.value.includes(missingCheckpointFile.value)
      ? [["checkpoint", missingCheckpointFile.value]]
      : []),
    ...SUPPORT_KINDS.flatMap((spec) =>
      (missingFiles.value[spec.kind] ?? []).map((file) => [spec.kind, file]),
    ),
  ];
  const results = await Promise.allSettled(
    asks.map(([kind, file]) =>
      readModelSwap(key, { replacing: file, slotKind: kind }),
    ),
  );
  if (check !== installedCheck || !stillOn(key)) return;
  const found = {};
  results.forEach((result, index) => {
    const [kind, file] = asks[index];
    if (result.status === "fulfilled") {
      found[`${kind}:${file}`] = result.value;
      return;
    }
    // Said on the row, not left as a missing picker nobody can explain.
    found[`${kind}:${file}`] = { replacements_reason: "unread" };
    console.warn(
      `[workflows] could not read replacements for ${file} on ${key}`,
      result.reason,
    );
  });
  replacementsByFile.value = found;
}
</script>

<style scoped>
.wftab-empty,
.wftab-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.wftab-empty {
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
}

.wftab-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The plugin catalogue link's shape (BehaviourSection.vue). */
.wftab-link {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.wftab-link:hover {
  text-decoration-thickness: 2px;
}

.wftab-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

/* "N pictures" on the left, the star histogram on the right. */
.wftab-sub {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
/* Underlined, not just coloured: it sits inside a line of secondary text, and
   a hue change alone would not say which words are the control. */
.wftab-pictures {
  /* `padding: 0; font: inherit` is the shipped inline-button reset
     (`EmptyScrapHeap.vue`): the global one drops the border and background
     but a <button> still inherits neither the UA padding nor its typeface,
     and this one sits mid-sentence in a line the template glues together
     whitespace-free. */
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.wftab-pictures:hover {
  color: rgb(var(--v-theme-primary));
}

.wftab-head {
  gap: var(--space-2);
}

/* The Workflow tab's body pads --space-4, not the inspector's --space-3, so no
   panel touches the rail edge; the 12px between panels is a clearly bigger
   step than the 8px between rows inside one. Only this tab: the stats charts
   on other tabs are drawn at a fixed width. */
.wftab--workflow :deep(.inspector-body) {
  gap: var(--space-4);
  padding-inline: var(--space-4);
}

/* A group drawn as a region of the pane, not a thing that floats: a hairline,
   the surface radius, a whisper of fill and no elevation. */
.wftab-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4) var(--space-4);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.025);
}

/* A panel like the three below it, on the error wash where a node failed: the
   `error_soft` ground, the label in `surface-error`, the sentence at full ink.
   A run somebody stopped keeps the plain panel. */
.wftab-run-failure:not(.wftab-run-failure--stopped) {
  border-color: transparent;
  background: rgba(var(--v-theme-error), 0.14);
}

.wftab-run-failure:not(.wftab-run-failure--stopped) .section-label {
  color: rgb(var(--v-theme-surface-error));
}

/* ComfyUI's own words can be one long token (a path, a tensor shape). */
.wftab-run-failure__message {
  overflow-wrap: anywhere;
}

/* One hairline between the two kinds of member of a group: Default and Also
   used, Set each run and Fixed. */
.wftab-rule {
  height: 1px;
  margin: var(--space-1) 0;
  background: rgb(var(--v-theme-divider));
}

.wftab-sublabel {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wftab-rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* The label column is --space-9, which "Checkpoint" fits at --text-xs.
   `minmax(0, 1fr)`, not `1fr`: a bare `1fr` track grows to its longest
   unbreakable word, and a file name then scrolls the whole inspector
   sideways. */
.wftab-field {
  display: grid;
  grid-template-columns: var(--space-9) minmax(0, 1fr);
  align-items: center;
  gap: var(--space-3);
}

.wftab-label {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A row whose value column can grow below its field (a meta line, the
   Checkpoint row's other checkpoints): the label stays level with the field,
   not centred on the whole column. */
.wftab-field--top {
  align-items: start;
}

.wftab-labelcol {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: var(--control-h);
}

/* "Yours": the one exception mark, a step above the label through weight and
   full ink, never hue; the same as `WorkflowDefaultRow`'s (design §2.1). */
.wftab-yours {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}

.wftab-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* Coverage on the left, Show N on the right, under the field, so the name
   keeps the whole field. Rendered only when it has content. */
.wftab-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-height: var(--control-h-sm);
  margin-top: var(--space-1);
}

.wftab-coverage {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.wftab-others {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  align-self: flex-start;
  min-height: var(--control-h-sm);
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}

.wftab-others-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.wftab-others-list > li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h-sm);
}

.wftab-others-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
}

.wftab-value {
  display: flex;
  align-items: center;
  min-height: var(--control-h);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* A panel's label with its verb on the right. The ghost verb is pulled out
   by its own padding, so its TEXT, not its hit area, lines up with the
   fields' edge. */
.wftab-sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  min-height: var(--control-h-sm);
  margin-right: calc(-1 * var(--space-3));
}

.wftab-sec-head > .section-label {
  min-width: 0;
}

.wftab-lora-value {
  gap: var(--space-2);
}

.wftab-chain-flag {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.wftab-chain-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wftab-chain-strength {
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wftab-missing,
.wftab-entries {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

/* A missing file's name is often one long word (`someModel_v6.safetensors`):
   break it anywhere rather than let it run past the column. */
.wftab-missing {
  overflow-wrap: anywhere;
}

/* A replaced base model: the value field as every other row draws it, with
   an icon-only Undo beside it. */
.wftab-fixed {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.wftab-fixed > .wftab-value {
  flex: 1;
  min-width: 0;
  gap: var(--space-2);
}

.wftab-fixed-flag {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

/* The hue drawn as text, so the surface variant: the fill is 2.1:1 on the
   light canvas. */
.wftab-warn {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-warning));
}

.wftab-disclose > summary {
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
  cursor: pointer;
}

.wftab-disclose > summary.wftab-sublabel {
  padding-top: 0;
  font-size: var(--text-xs);
}

.wftab-notes {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: inherit;
  font: inherit;
  font-size: var(--text-sm);
  resize: vertical;
}

.wftab-mono {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  overflow-wrap: anywhere;
}

.wftab-foot {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  /* The body's inline padding, so the buttons line up with the content while
     the hairline spans the whole rail. */
  padding: var(--space-3) var(--space-4);
  /* `border`, not `divider`: divider all but vanishes on the sidebar tone. */
  border-top: 1px solid rgb(var(--v-theme-border));
}

.wftab-foot > :first-child {
  flex: 1;
}

.wftab-foot > .wftab-note {
  flex-basis: 100%;
}

.wftab-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}

.wftab-item:hover {
  background: var(--hover-wash);
}
</style>
