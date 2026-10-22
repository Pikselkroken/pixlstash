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
        <!-- Which version the workflow is on, and the way back to an earlier
             one (#1850). Only where there is one to go back to. -->
        <WorkflowVersionMenu
          v-if="headVersion"
          ref="versionMenu"
          :number="headVersion.number"
          :at="headVersion.at"
          :versions="shownVersions?.versions ?? null"
          :failed="versionsFailed"
          @open="reopenVersions"
          @go-back="goBack"
        />
        <!-- The newest version is one ComfyUI saved (#1852): said, with the
             way back beside it, and nothing to answer. An inline button in a
             line of secondary text, as "N pictures" above is. -->
        <p
          v-if="fromComfyui"
          class="wftab-sublabel"
          data-testid="wftab-from-comfyui"
        >
          Saved from ComfyUI {{ fromComfyui.when }} ·
          <button
            class="wftab-pictures"
            type="button"
            data-testid="wftab-go-back"
            :aria-label="`Go back to version ${fromComfyui.back}`"
            :disabled="busy === 'version'"
            @click="goBack({ version: fromComfyui.back })"
          >
            Go back
          </button>
        </p>
      </div>

      <!-- Going back was refused: said under the control that asked, in the
           server's words, and an `alert` because it answers a click. -->
      <section
        v-if="versionError"
        class="wftab-panel wftab-run-failure"
        role="alert"
        data-testid="wftab-version-error"
      >
        <div class="wftab-sec-head">
          <span class="section-label" role="heading" aria-level="3"
            >Could not go back</span
          >
          <AppButton
            size="sm"
            variant="ghost"
            data-testid="wftab-version-error-dismiss"
            @click="dismissVersionError"
          >
            Dismiss
          </AppButton>
        </div>
        <p class="wftab-note wftab-run-failure__message">
          {{ versionError }}
        </p>
      </section>

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
          <span
            ref="modelsHeading"
            class="section-label"
            role="heading"
            aria-level="3"
            tabindex="-1"
            >Models</span
          >
          <!-- Fills the rows below from one of the owner's model sets, as
               unsaved changes: nothing is written and no workflow is made. -->
          <WorkflowUseSetMenu
            ref="useSetMenu"
            :workflow-id="selectedKey || ''"
            :models="settableModels"
            :disabled="chainNoGraph || !settableModels.length || !canEdit"
            :disabled-reason="
              chainNoGraph
                ? 'wftab-chain-reason'
                : changesFailed
                  ? 'wftab-changes-failed'
                  : undefined
            "
            @use="useSet"
          />
        </div>
        <!-- Every row below is a view of the unsaved changes, so none can be
             edited until they are read. A read that failed says so here, with
             the way to ask again, rather than leave the rows locked for no
             reason anybody can see. -->
        <p
          v-if="changesFailed"
          id="wftab-changes-failed"
          class="wftab-note wftab-quiet"
          data-testid="wftab-changes-failed"
        >
          Could not read this workflow's unsaved changes just now, so its
          models, stages and LoRAs cannot be changed.
          <AppButton
            size="sm"
            variant="ghost"
            data-testid="wftab-changes-retry"
            @click="retryChanges"
          >
            Retry
          </AppButton>
        </p>
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
        <!-- One row per loader the default recipe names, and every one a
             picker (#1850): a pick joins the unsaved changes, it is not
             written. A missing file is a row that needs a pick, with the
             warning kept above its field. -->
        <div
          v-for="row in modelRows"
          :key="row.address"
          class="wftab-field wftab-field--top"
          :data-testid="`wftab-row-${row.kind}`"
          :data-address="row.address"
        >
          <span class="wftab-labelcol">
            <span v-if="row.first" class="wftab-label">{{ row.heading }}</span>
            <span
              v-if="row.edited"
              class="wftab-yours wftab-yours--unsaved"
              data-testid="wftab-edited"
              >{{ row.fix ? "Fixed" : "Edited" }}</span
            >
            <span v-else-if="row.yours" class="wftab-yours">Yours</span>
          </span>
          <div class="wftab-col wftab-model">
            <template v-if="row.missing && !row.edited">
              <p class="wftab-warn">
                <v-icon size="16">mdi-alert-outline</v-icon>
                {{ row.missingText }}
              </p>
              <p class="wftab-note wftab-quiet" data-testid="wftab-missing-file">
                <span class="wftab-file"
                  ><Tooltip :text="row.missing" activator="parent" />{{
                    modelLabel(fileName(row.missing))
                  }}</span
                >{{ missingNote(row.missing) }}
              </p>
              <!-- A pick is saved on the workflow, so an offer that is not of
                   the missing one's base model says so before it. -->
              <p
                v-if="row.unmatched"
                class="wftab-note wftab-quiet"
                data-testid="wftab-replace-unmatched"
              >
                {{ UNMATCHED_REPLACEMENTS_TEXT }}
              </p>
            </template>
            <AppSelect
              v-if="row.options.length"
              :model-value="row.pick ?? ''"
              :label="row.name"
              hide-label
              :options="row.options"
              :disabled="!canEdit"
              :data-testid="`wftab-pick-${row.kind}`"
              @update:model-value="(file) => pickModel(row, file)"
            />
            <span v-else class="wftab-value"
              ><Tooltip v-if="row.file" :text="row.file" activator="parent" />{{
                row.savedLabel
              }}</span
            >
            <!-- What the shelf files this loader's model as, where every
                 workflow loading it says it is this loader's kind (#1962): the
                 shelf's Kind menu, here too. It is the one note under the
                 picker, so "Nothing to offer" gives way to it. -->
            <KindMenu
              v-if="row.offer"
              :file-kind="row.offer.file_kind"
              :kind-text="
                row.offer.copy
                  ? `${modelLabel(fileName(row.offer.copy.filename))}: ${fileKindLabel(row.offer.file_kind)} on shelf`
                  : `${fileKindLabel(row.offer.file_kind)} on shelf`
              "
              :subject="
                row.offer.copy
                  ? modelLabel(fileName(row.offer.copy.filename))
                  : row.savedLabel
              "
              :suggested-kind="row.offer.suggested_kind"
              :suggested-by="row.offer.suggested_by"
              :suggested-fields="row.offer.suggested_fields"
              :evidence-heading="
                row.offer.copy ? 'Named like the missing file' : undefined
              "
              :evidence-note="row.offer.copy ? copyNote(row) : ''"
              :adapter="false"
              @pick="(kind) => fileOffered(row, { file_kind: kind })"
              @suggest="(on) => fileOffered(row, { suggest_kind: on })"
            />
            <p
              v-else-if="row.missing && !row.options.length && row.noOptions"
              class="wftab-note wftab-quiet"
              :data-testid="`wftab-no-replacement-${row.kind}`"
            >
              {{ row.noOptions }}
            </p>
            <!-- The saved value stays readable under a changed field, with
                 the way back beside it. -->
            <div v-if="row.edited" class="wftab-meta" data-testid="wftab-saved">
              <span class="wftab-coverage wftab-quiet"
                ><span
                  v-if="row.fix"
                  class="wftab-fix-mark"
                  tabindex="0"
                  :aria-label="`How ${row.name} was fixed`"
                  data-testid="wftab-fix-mark"
                  :data-note="autoFixNote(row.fix)"
                  ><Tooltip :text="autoFixNote(row.fix)" activator="parent" /><v-icon
                    size="16"
                    aria-hidden="true"
                    >mdi-auto-fix</v-icon
                  ></span
                ><span
                  ><Tooltip :text="row.file" activator="parent" />{{
                    row.fix ? "Corrected:" : "Saved:"
                  }}
                  {{ row.savedLabel }}</span
                ></span
              >
              <AppButton
                variant="ghost"
                size="sm"
                icon-only
                icon-left="undo"
                data-testid="wftab-reset-model"
                :disabled="!canEdit"
                :tooltip="`Put ${row.name} back to ${row.savedLabel}`"
                @click="pickModel(row, '')"
              />
            </div>
            <template v-if="row.kind === 'checkpoint' && row.first">
              <!-- Show N (§1.5): only below the workflow's own count, which
                   the head's link already shows. -->
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
              <!-- The other checkpoints its pictures used: a disclosure, not
                   a menu, because each is a row with its own Show N. -->
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
            </template>
          </div>
        </div>
        <!-- No loader of the default recipe names a base model: what the
             card and the graph a run submits say instead. Text, because there
             is no loader address to write a pick to. -->
        <div
          v-if="!modelRows.some((row) => row.kind === 'checkpoint')"
          class="wftab-field wftab-field--top"
          data-testid="wftab-row-checkpoint"
        >
          <span class="wftab-labelcol">
            <span class="wftab-label">{{
              checkpointCount > 1 ? "Checkpoints" : "Checkpoint"
            }}</span>
          </span>
          <div class="wftab-col wftab-model">
            <!-- Missing: ComfyUI does not have the file (the run pre-flight's
                 answer), or - when ComfyUI cannot be asked - the card has no
                 name for it. Always with the FILE it is missing; the whole
                 recorded value is the tooltip. -->
            <template v-if="checkpointIsMissing">
              <p class="wftab-warn">
                <v-icon size="16">mdi-alert-outline</v-icon>
                {{
                  staleFiles.has(missingCheckpointFile)
                    ? PACK_OUTDATED_TEXT
                    : "Checkpoint missing"
                }}
              </p>
              <p
                v-if="missingCheckpointFile"
                class="wftab-note wftab-quiet"
                data-testid="wftab-missing-file"
              >
                <span class="wftab-file"
                  ><Tooltip :text="missingCheckpointFile" activator="parent" />{{
                    modelLabel(fileName(missingCheckpointFile))
                  }}</span
                >{{ missingNote(missingCheckpointFile) }}
              </p>
              <p v-else class="wftab-note wftab-quiet">
                No file name was kept for it anywhere.
              </p>
            </template>
            <span v-else-if="checkpointLabel" class="wftab-value">{{
              checkpointLabel
            }}</span>
            <!-- The card has no name for it, but the graph a run submits
                 does: that is the file, installed as far as anybody knows. -->
            <span v-else-if="graphBaseModel" class="wftab-value"
              ><Tooltip :text="graphBaseModels" activator="parent" />{{
                detail.graph_base_models
                  .map((file) => modelLabel(fileName(file)))
                  .join(" + ")
              }}</span
            >
            <!-- The graph a run would submit was read and loads no base
                 model: an upscaler, say. A fact, so it is said as one. -->
            <span v-else-if="graphLoadsNone" class="wftab-value wftab-quiet">
              None in this workflow
            </span>
            <!-- A recipe-less card's rows are what its file gave up, so an
                 empty one is "not read", never a claim that it has none. -->
            <span v-else-if="checkpointIsUnread" class="wftab-value wftab-quiet">
              Not read from its file
            </span>
            <!-- Only when nothing anywhere names a base model: not the card,
                 not the graph a run would submit, not the pre-flight. -->
            <span v-else class="wftab-value wftab-quiet">Not recorded</span>
          </div>
        </div>
        <div
          v-if="!modelRows.some((row) => row.kind === 'vae')"
          class="wftab-field"
          data-testid="wftab-row-vae"
        >
          <span class="wftab-label">VAE</span>
          <span class="wftab-value">From the checkpoint</span>
        </div>

        <!-- Each optional stage of the base graph, and whether the workflow
             runs it. A toggle joins the unsaved changes. Absent when the
             graph has none. -->
        <div
          v-if="stageRows.length"
          class="wftab-field wftab-field--top"
          role="group"
          aria-label="Stages"
          data-testid="wftab-stages"
        >
          <span class="wftab-labelcol">
            <span class="wftab-label">Stages</span>
            <span
              v-if="stageRows.some((stage) => stage.edited)"
              class="wftab-yours wftab-yours--unsaved"
              data-testid="wftab-stages-edited"
              >Edited</span
            >
          </span>
          <div class="wftab-checks">
            <label
              v-for="stage in stageRows"
              :key="stage.key"
              class="wftab-check"
            >
              <Tooltip
                v-if="stage.detail"
                :text="stage.detail"
                activator="parent"
              />
              <input
                type="checkbox"
                :checked="stage.on"
                :disabled="!canEdit"
                :data-testid="`wftab-stage-${stage.key}`"
                @change="toggleStage(stage, $event.target.checked)"
              />
              {{ stage.label }}
            </label>
          </div>
        </div>
        <p
          v-if="stageVoteNote"
          class="wftab-note wftab-quiet"
          data-testid="wftab-stage-note"
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
      <section ref="loraPanel" class="wftab-panel" data-testid="wftab-loras">
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
            ref="editLorasButton"
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
          v-for="(lora, index) in loraRows"
          :key="lora.id"
          class="wftab-field wftab-field--top"
          data-testid="wftab-default-lora"
          :data-slot="lora.slotKey || undefined"
        >
          <span class="wftab-labelcol">
            <span v-if="!index" class="wftab-label">Default</span>
            <span
              v-if="lora.removed"
              class="wftab-yours wftab-yours--unsaved"
              data-testid="wftab-lora-removed"
              >Removed</span
            >
            <span
              v-else-if="lora.strengthEdited"
              class="wftab-yours wftab-yours--unsaved"
              data-testid="wftab-lora-edited"
              >Edited</span
            >
            <span v-else-if="lora.edited" class="wftab-yours">Yours</span>
          </span>
          <div class="wftab-col">
            <!-- Removed, until the save: the row stays, struck through, with
                 the way back where the × was. -->
            <span
              v-if="lora.removed"
              class="wftab-value wftab-lora-value wftab-lora-gone"
            >
              <s class="wftab-chain-name"
                ><Tooltip v-if="lora.file" :text="lora.file" activator="parent" />{{
                  lora.label
                }}</s
              >
              <AppButton
                size="sm"
                variant="ghost"
                data-testid="wftab-lora-put-back"
                :disabled="!canEdit"
                :aria-label="`Put ${lora.label} back`"
                @click="setLoraRemoved(lora, false)"
              >
                Put back
              </AppButton>
            </span>
            <span v-else class="wftab-value wftab-lora-value">
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
              <!-- The strength is edited where it is read. Only a loader the
                   graph has, holding a LoRA the shelf knows, can be named in
                   a change; any other keeps its strength as text. A
                   component, so an answer landing while a number is half
                   typed does not put the row's back. -->
              <WorkflowStrengthInput
                v-if="lora.canStrength"
                class="wftab-strength"
                step="0.05"
                min="-10"
                max="10"
                :value="lora.strengthValue"
                :disabled="!canEdit"
                :aria-label="`Strength of ${lora.label}`"
                data-testid="wftab-lora-strength"
                @change="(raw, field) => setLoraStrength(lora, raw, field)"
              />
              <span v-else-if="lora.strengthText" class="wftab-chain-strength">{{
                lora.strengthText
              }}</span>
              <AppButton
                v-if="lora.slot"
                variant="ghost"
                size="sm"
                icon-only
                icon-left="close"
                data-testid="wftab-lora-remove"
                :disabled="!canEdit"
                :tooltip="`Remove ${lora.label}`"
                @click="setLoraRemoved(lora, true)"
              />
            </span>
            <div
              v-if="lora.strengthEdited && !lora.removed"
              class="wftab-meta"
              data-testid="wftab-lora-saved"
            >
              <span class="wftab-coverage wftab-quiet"
                >Saved: {{ lora.savedStrengthText }}</span
              >
              <AppButton
                variant="ghost"
                size="sm"
                icon-only
                icon-left="undo"
                data-testid="wftab-reset-strength"
                :disabled="!canEdit"
                :tooltip="`Put the strength of ${lora.label} back to ${lora.savedStrengthText}`"
                @click="setLoraStrength(lora, lora.savedStrength)"
              />
            </div>
            <div
              v-if="!lora.removed && (lora.coverage || lora.show)"
              class="wftab-meta"
            >
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
          or changed.
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
        <p
          v-if="detailPending || (formHeld && defaults.length)"
          class="wftab-note wftab-quiet"
        >
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
                 Pinned group" is containment rather than document order. -->
            <div class="wftab-rows" data-testid="wftab-fixed-group">
              <!-- More than three fixed rows fold away behind their count. -->
              <details
                v-if="fixedDefaults.length > FIXED_SHOWN"
                class="wftab-disclose"
              >
                <summary class="wftab-sublabel">
                  Pinned
                  <span class="wftab-count">{{ fixedDefaults.length }}</span>
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
                  Pinned
                  <span class="wftab-count">{{ fixedDefaults.length }}</span>
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
                Pinned values are used by every run.
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
    </template>

    <!-- The inspector's footer slot sits below the scrolling body, so Run…
         stays where the design puts it and never scrolls. -->
    <!-- The Workflow tab's, and only its: Recipes runs a recipe from its own
         row and Tasks is the app's business, so neither wants this footer. -->
    <template #footer>
      <!-- The first change is said once, politely, and focus stays on the row
           that made it. Outside the bar, so it is in the page before its
           text is: a live region that arrives with its text already in it
           announces nothing. Emptied once the count has moved on, so nobody
           reading the page finds an old number here. -->
      <p class="visually-hidden" role="status" data-testid="wftab-draft-arrival">
        {{ arrival }}
      </p>
      <!-- The unsaved changes of the graph, and the two verbs that keep them
           (#1850). Above Run…, and only while something waits. A count, never
           a list: the rows above show which. Which verbs are offered, and the
           sentence saying why one is not, are the server's, so this bar, the
           Run popup and the Tasks tab cannot disagree.

           Teal is "not saved yet": the band, the filled Save and the dot
           before a row's Edited are one mark, so the eye is led from the row
           just changed to the place that keeps it. Amber stays Run…'s. -->
      <div
        v-if="barOf"
        ref="draftBar"
        :class="['wftab-draft', { 'wftab-draft--arrived': arrived }]"
        role="group"
        aria-label="Unsaved changes"
        data-testid="wftab-draft"
      >
        <div class="wftab-draft__line">
          <span class="wftab-draft__count" data-testid="wftab-draft-count">
            <v-icon size="16" aria-hidden="true">{{
              autoFixCount && autoFixCount >= changeCount
                ? "mdi-auto-fix"
                : "mdi-pencil"
            }}</v-icon>
            {{ draftText(changeCount) }}
          </span>
          <AppButton
            size="sm"
            variant="ghost"
            :disabled="Boolean(settlingShown)"
            :loading="settlingShown === 'discard'"
            data-testid="wftab-discard"
            @click="discardChanges"
          >
            Discard
          </AppButton>
        </div>
        <!-- Save as new… turns the bar into its one question, the name. -->
        <form
          v-if="naming && saveAsNewOpen"
          class="wftab-draft__name"
          @submit.prevent
        >
          <input
            ref="nameField"
            v-model="newName"
            class="wftab-draft__input"
            type="text"
            placeholder="Name of the new workflow"
            aria-label="Name of the new workflow"
            data-testid="wftab-new-name"
            @keydown.enter.prevent="saveAsNew"
            @keydown.esc.stop.prevent="stopNaming"
          />
          <div class="wftab-draft__verbs">
            <AppButton
              variant="ghost"
              data-testid="wftab-new-name-back"
              @click="stopNaming"
            >
              Back
            </AppButton>
            <AppButton
              variant="save"
              :disabled="!newName.trim() || Boolean(settlingShown)"
              :loading="settlingShown === 'save-as-new'"
              data-testid="wftab-save-as-new-confirm"
              @click="saveAsNew"
            >
              Save as new
            </AppButton>
          </div>
        </form>
        <div
          v-else-if="saveOpen || saveAsNewOpen"
          class="wftab-draft__verbs"
        >
          <!-- The other verb: an outline, its border in the shelf's teal. Where
               Save is closed it is the one verb that keeps, and is drawn as
               that one. -->
          <AppButton
            v-if="saveAsNewOpen"
            ref="saveAsNewButton"
            :variant="saveOpen ? 'outline' : 'save'"
            :class="saveOpen ? 'wftab-draft__other' : 'wftab-draft__save'"
            :disabled="Boolean(settlingShown)"
            data-testid="wftab-save-as-new"
            @click="startNaming"
          >
            Save as new…
          </AppButton>
          <!-- The ordinary verb: filled, at the full control height, and as
               wide as the row leaves it. Teal, never amber: Run… stays the
               one amber button. -->
          <AppButton
            v-if="saveOpen"
            variant="save"
            class="wftab-draft__save"
            icon-left="content-save-outline"
            :disabled="Boolean(settlingShown)"
            :loading="settlingShown === 'save'"
            data-testid="wftab-save"
            @click="saveChanges"
          >
            Save
          </AppButton>
        </div>
        <!-- A verb that is not offered is not drawn greyed out: its reason
             is said instead, exactly as the server sent it. -->
        <p
          v-for="reason in closedReasons"
          :key="reason"
          class="wftab-note"
          data-testid="wftab-draft-reason"
        >
          {{ reason }}
        </p>
        <p
          v-if="draftError"
          class="wftab-note"
          role="alert"
          data-testid="wftab-draft-error"
        >
          {{ draftError }}
        </p>
      </div>
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
          {{ changeCount && !multiple ? "Run with changes…" : "Run…" }}
        </AppButton>
        <!-- Beside Run…, as the ComfyUI mark: the ComfyUI-PixlStash node reads
             `?pixlstash_workflow=` and loads the graph, so without the node
             ComfyUI opens on whatever it had last. It opens what Run… runs
             (`runTarget`), and is refused rather than hidden otherwise, for
             Run…'s reason or a ComfyUI without the node. The tooltip is its
             accessible name. What opens has the changes waiting here in it,
             and what ComfyUI saves from there comes back as this workflow's
             next version, those changes with it (#1852). Changes made on a
             version the workflow has since left are not in it: the server
             leaves them out. With nodes from before that, what opens is a
             copy of the SAVED graph and the name says so. While changes wait,
             the line below says which of the three. -->
        <AppButton
          v-if="canOpenComfyui"
          icon-only
          :tooltip="comfyuiOpensACopy ? 'Open a copy in ComfyUI' : 'Open in ComfyUI'"
          data-testid="wftab-open-comfyui"
          :aria-disabled="runTarget && !comfyuiLacksNode ? undefined : 'true'"
          :aria-describedby="openDescribedBy"
          @click="openInComfyui()"
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
              <!-- Open in ComfyUI opens the workflow with ComfyUI-PixlStash
                   nodes in place of its savers and LoRA loaders, so what is
                   queued there comes back. This opens it with its own nodes.
                   Offered only where the button beside it would open. -->
              <button
                v-if="canOpenComfyui && runTarget && !comfyuiLacksNode"
                class="wftab-item"
                type="button"
                data-testid="wftab-open-original"
                @click="openOriginal"
              >
                <ComfyuiIcon :size="16" />
                Open original in ComfyUI
              </button>
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
          node in ComfyUI. Install or update it, then restart ComfyUI. Already
          installed? Restart ComfyUI, then leave Workflows and come back so
          PixlStash checks again.
        </p>
        <p
          v-else-if="canOpenComfyui && changeCount"
          id="wftab-open-unsaved"
          class="wftab-note wftab-quiet"
          data-testid="wftab-open-unsaved"
        >
          <template v-if="changesStale">
            Open in ComfyUI opens the workflow as it is now. The unsaved
            changes were made on an earlier version and are not in it.
          </template>
          <template v-else-if="comfyuiOpensACopy">
            Open in ComfyUI opens the saved workflow. The unsaved changes are
            not in it until they are saved.
          </template>
          <template v-else>
            Open in ComfyUI opens the workflow with the unsaved changes in it.
            Saving there saves them too.
          </template>
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
      :drop-lora="editDrop"
      @close="closeEditLoras"
      @apply="applyLoraEdit"
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

import {
  computed,
  nextTick,
  onBeforeUnmount,
  reactive,
  ref,
  toRaw,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import {
  discardWorkflowChanges,
  getLoraChain,
  getLoraSummary,
  getWorkflowCard,
  getWorkflowChanges,
  getWorkflowFormInputs,
  getWorkflowVersions,
  patchWorkflowCard,
  preflightWorkflowRun,
  readModelSwap,
  restoreWorkflowVersion,
  saveWorkflow,
  saveWorkflowAsNew,
  setWorkflowChanges,
  setWorkflowDefaultLora,
  setWorkflowDefaults,
  setWorkflowPins,
  workflowCoverUrl,
} from "../../api/workflows";
import {
  openWorkflowInComfyui,
  useComfyuiOpen,
} from "../../composables/useComfyuiOpen";
import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { modelLabel } from "../../utils/filterChips";
import {
  EDIT_LORAS,
  formatStrength,
  loraBase,
  loraStem,
} from "../../utils/loraChain";
import { editModels } from "../../api/modelShelf";
import { fileKindLabel, quantBadge } from "../../utils/modelShelf";
import {
  PACK_OUTDATED,
  PACK_OUTDATED_ACTION,
  PIXLSTASH_PACK_URL,
  STAGE_LABELS,
} from "../../utils/runReasons";
import { relativeDate } from "../../utils/snapshots";
import {
  checkpointMissing,
  baseModels,
  checkpointUnread,
  modelDisplayName,
  NO_REPLACEMENT_TEXT,
  outputCount,
  replacementLabel,
  UNMATCHED_REPLACEMENTS_TEXT,
} from "../../utils/workflowCard";
import {
  choicesFor,
  nodesByAddress,
  optionsByAddress,
  parameterAddress,
  setEachRun,
} from "../../utils/workflowPins";
import { slotOf, whole } from "../../utils/workflowChanges";
import { pictureCount } from "../../utils/workflowSets";
import {
  countChanges,
  loraRemoved,
  loraStrength,
  modelPick,
  stageOn,
  withFragment,
  withLoraRemoved,
  withLoraStrength,
  withModel,
  withoutGoneLoaders,
  withStage,
} from "../../utils/workflowEdits";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import AppSelect from "../widgets/AppSelect.vue";
import BreakableTitle from "../widgets/BreakableTitle.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import EditLorasDialog from "../io/EditLorasDialog.vue";
import KindMenu from "../widgets/KindMenu.vue";
import ExposeParameterDialog from "../io/ExposeParameterDialog.vue";
import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowDefaultRow from "./WorkflowDefaultRow.vue";
import WorkflowStarChart from "./WorkflowStarChart.vue";
import WorkflowLoraPile from "./WorkflowLoraPile.vue";
import WorkflowRecipesTab from "./WorkflowRecipesTab.vue";
import WorkflowStrengthInput from "./WorkflowStrengthInput.vue";
import WorkflowUseSetMenu from "./WorkflowUseSetMenu.vue";
import WorkflowVersionMenu from "./WorkflowVersionMenu.vue";

/** Past this many, the Pinned group folds away behind "Pinned N". */
const FIXED_SHOWN = 3;

const store = useWorkflowsStore();
const { showPictures, showLoraPictures, showValuePictures } =
  useWorkflowPictures();
const sidebarStore = useSidebarStore();
const notices = useNoticeStore();
const { canOpenComfyui, comfyuiLacksNode, comfyuiOpensACopy } =
  useComfyuiOpen();
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

/**
 * The versions kept of a workflow, as `GET …/versions` answered, with the
 * workflow they are of: `{ key, version, versions }`. One read for the shown
 * workflow, never one per card of the grid.
 */
const versionsRead = ref(null);
const versionsFailed = ref(false);
/** The server's sentence for a go-back it refused, or "". */
const versionError = ref("");
const versionMenu = ref(null);
/** Which versions read is the latest; a go-back's answer also counts as one. */
let versionsAsk = 0;

/** The read, when it is of the workflow on screen: never another's. */
const shownVersions = computed(() =>
  versionsRead.value?.key === selectedKey.value ? versionsRead.value : null,
);

/**
 * "Version 3 · 2 Oct" in the head: only where there is a history to speak of,
 * a workflow stored more than once. An automatic workflow nobody has saved
 * over has no versions and no control; nothing is invented for it.
 *
 * The read's own current row once it is in, so a go-back moves the head with
 * its answer; until then the card's, which the grid already carries.
 * `version`, the number, not `versions`, how many are kept: past the 50 kept
 * the two differ.
 */
const headVersion = computed(() => {
  const c = card.value;
  if (!c) return null;
  const read = shownVersions.value;
  if (read) {
    const current = read.versions.find((row) => row.current);
    return current && read.versions.length > 1
      ? { number: current.version, at: current.created_at }
      : null;
  }
  return c.versions > 1 ? { number: c.version, at: c.version_at ?? null } : null;
});

// A workflow opened in ComfyUI from here saves back to itself, with no
// question in either app (#1852). So when the newest version is one ComfyUI
// saved, this tab says so in one line and offers the way back, without the
// menu having to be opened to learn of it.
const fromComfyui = computed(() => {
  const [newest, before] = shownVersions.value?.versions ?? [];
  // By what the version is, not by the menu's words for it: a file the
  // removed pull read is worded the same and was not saved from a canvas.
  if (!before || newest?.source !== "comfyui") return null;
  return { when: relativeDate(newest.created_at), back: before.version };
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
  return file ? modelLabel(fileName(file)) : "Unnamed shelf model";
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
 * How many base models the row prints where no loader of the default recipe
 * names one. The graph's list is one entry per loaded file already, and two
 * shelf ids the shelf no longer holds are two entries reading alike.
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
 * The kinds of model a row is drawn for, by the default recipe's `kind` (the
 * model-fix `slot_kind`), each with the pre-flight's folders for it.
 * `clip_vision` is not `text_encoders`, so a vision encoder is never offered a
 * text encoder.
 */
const MODEL_ROWS = {
  checkpoint: {
    heading: "Checkpoint",
    missingText: "Checkpoint missing",
    folders: BASE_MODEL_FOLDERS,
  },
  vae: { heading: "VAE", missingText: "VAE missing", folders: new Set(["vae"]) },
  text_encoder: {
    heading: "Text encoder",
    missingText: "Text encoder missing",
    folders: new Set(["text_encoders"]),
  },
};

/** What the pre-flight reports for a name the hub forgot: names no file. */
const FORGOTTEN_MODEL = "(forgotten model)";

/**
 * The base-model files the run pre-flight says ComfyUI does not have.
 *
 * The card cannot answer this: it names what the recipe recorded, and only
 * ComfyUI's own model list says whether that file is installed. So the tab
 * asks the same question Run… asks, once per card. `preflightAnswered` says
 * whether it has: an unreachable ComfyUI leaves the card's own answer.
 */
const missingByKind = ref({});
/** The missing files a restart of ComfyUI would load off the shelf (#1923). */
const staleFiles = ref(new Set());
const PACK_OUTDATED_TEXT = "Restart ComfyUI";
/** What follows a missing file's name, once ComfyUI has answered. */
function missingNote(file) {
  if (!preflightAnswered.value) return "";
  return staleFiles.value.has(file)
    ? ` is on your model shelf, but the ComfyUI-PixlStash running in ComfyUI is out of date and cannot load it. ${PACK_OUTDATED_ACTION}`
    : " is not installed in ComfyUI.";
}
const missingBaseFiles = computed(() => missingByKind.value.checkpoint ?? []);
const preflightAnswered = ref(false);
let installedCheck = 0;
// A rail that has closed asks nothing more: an ask still out is superseded.
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

/** The file that is missing, for a card with no model row to say it on. */
const missingCheckpointFile = computed(
  () =>
    missingBaseFiles.value.find((file) => file !== FORGOTTEN_MODEL) ||
    graphBaseModel.value ||
    checkpointLabel.value,
);

/**
 * `GET …/model-swap?replacing=` per model row, keyed `kind:file`: the shelf
 * models that go with the workflow's checkpoint and that the file's loader
 * can load (`replacements`), and why there are none (`replacements_reason`).
 * Read for every row, missing or not: each is a picker (#1850).
 */
const replacementsByFile = ref({});

/** Whether two recorded values name one file, whatever their folders. */
function sameFile(a, b) {
  return loraBase(a) === loraBase(b);
}

/**
 * The file a default-recipe model's loader loads today: the owner's earlier
 * replacement where one was made (`model_fixes`, which the server still
 * applies), else the recorded one.
 */
function loadedFile(model) {
  const fix = (detail.value?.model_fixes ?? []).find(
    (entry) =>
      entry.slot_kind === model.kind && sameFile(entry.was, model.filename),
  );
  return fix ? fix.now : model.filename;
}

/**
 * Whether a model row can take a pick. A shelf loader's value is a shelf row
 * id, not a file, and the model-swap read does not answer for it.
 */
function settable(model) {
  return !String(model.address ?? "").endsWith("/checkpoint_id");
}

/** A candidate as a picker option names it: never a bare filename. */
function candidateLabel(model) {
  return replacementLabel({
    ...model,
    filename: modelLabel(fileName(model.filename)),
  });
}

/** Why a missing file's row has nothing to pick, or "". */
function noReplacementText(kind, answer) {
  if (!answer || answer.replacements?.length) return "";
  const reason = answer.replacements_reason;
  if (kind !== "checkpoint") return NO_REPLACEMENT_TEXT[reason] ?? "";
  if (reason === "none_loadable")
    return "No checkpoint on your shelf that could replace it is one this loader can load.";
  return ["none_go_with_it", "unread"].includes(reason)
    ? NO_REPLACEMENT_TEXT[reason]
    : "Nothing on your shelf can replace it.";
}

/**
 * The model rows: one per loader the default recipe names, each a picker
 * where the shelf has something its loader can take.
 *
 * `pick` is the file waiting, unsaved, at that loader's address; the saved
 * file is the picker's first option, so choosing it takes the change back out
 * of the set. A pick the candidates do not list (one a model set filled in) is
 * listed too, or the field would read as saved while the row says Edited.
 */
const rowModels = computed(() =>
  (recipeModels.value ?? []).filter(
    (model) => MODEL_ROWS[model.kind] && model.filename,
  ),
);

/**
 * Which row each missing file is, by loader address.
 *
 * The pre-flight names a file as the graph spells it, and the recipe may
 * spell the same loader's file another way (a quant build, a renamed file).
 * So a missing file goes to the row naming it, and one no row names goes to
 * the first row of its kind that has none: a missing checkpoint is the
 * Checkpoint row's, whatever the two call it.
 */
const missingByAddress = computed(() => {
  const found = {};
  for (const kind of Object.keys(MODEL_ROWS)) {
    const rows = rowModels.value.filter(
      (model) => model.kind === kind && settable(model),
    );
    const unnamed = [];
    for (const gone of missingByKind.value[kind] ?? []) {
      if (gone === FORGOTTEN_MODEL) continue;
      const row = rows.find(
        (model) => !found[model.address] && sameFile(loadedFile(model), gone),
      );
      if (row) found[row.address] = gone;
      else unnamed.push(gone);
    }
    for (const gone of unnamed) {
      const row = rows.find((model) => !found[model.address]);
      if (row) found[row.address] = gone;
    }
  }
  return found;
});

const modelRows = computed(() => {
  const models = rowModels.value;
  const seen = {};
  return models.map((model) => {
    const spec = MODEL_ROWS[model.kind];
    const nth = (seen[model.kind] = (seen[model.kind] ?? 0) + 1);
    const several =
      models.filter((other) => other.kind === model.kind).length > 1;
    const canPick = settable(model);
    // The graph's own spelling where the pre-flight gave it: that is what
    // the server matches a loader on.
    const gone = missingByAddress.value[model.address] ?? "";
    const file = canPick
      ? gone || loadedFile(model)
      : model.shelf_filename || "";
    const answer = canPick
      ? replacementsByFile.value[`${model.kind}:${file}`]
      : null;
    const candidates = (answer?.replacements ?? []).filter(
      (candidate) => !sameFile(candidate.filename, file),
    );
    const pick = canPick ? modelPick(waiting.value, model.address) : null;
    const savedLabel =
      canPick && file !== model.filename
        ? modelLabel(fileName(file))
        : recipeModelLabel(model);
    return {
      address: model.address,
      kind: model.kind,
      // "Checkpoints" over a pair (Wan 2.2 high + low): one label for the
      // rows of a kind, and which loader is which is not claimed.
      heading:
        several && model.kind === "checkpoint" ? "Checkpoints" : spec.heading,
      first: nth === 1,
      // Said in full where the label column cannot: two of a kind share one
      // visible label, and a picker's name has to tell them apart.
      name: several ? `${spec.heading} ${nth}` : spec.heading,
      settable: canPick,
      file,
      savedLabel,
      pick,
      edited: Boolean(pick),
      fix: autoFixOf(gone, pick, candidates),
      yours: model.provenance === "edited",
      missing: gone,
      missingText: staleFiles.value.has(gone)
        ? PACK_OUTDATED_TEXT
        : spec.missingText,
      unmatched:
        model.kind === "checkpoint" &&
        candidates.length > 0 &&
        answer?.replacements_narrowed === false,
      options:
        candidates.length || pick
          ? [
              // A missing file is no choice: said, or it reads as picked.
              { value: "", label: gone ? `${savedLabel} (missing)` : savedLabel },
              ...candidates.map((candidate) => ({
                value: candidate.filename,
                label: candidateLabel(candidate),
              })),
              ...(pick &&
              !candidates.some((candidate) => candidate.filename === pick)
                ? [{ value: pick, label: modelLabel(fileName(pick)) }]
                : []),
            ]
          : [],
      noOptions: noReplacementText(model.kind, answer),
      // Only on the file the loader still loads: a replacement is a shelf
      // model of the loader's own kind already.
      offer: kindOfferFor(model, canPick, file, gone, pick, answer),
    };
  });
});

/**
 * The shelf's kind offer on a model row (#1962), or null.
 *
 * Its own file, where every workflow loading it says it is this loader's kind
 * (`kind_offers`): only on the file the loader still loads, since a
 * replacement is a shelf model of the loader's own kind already. Or, on a
 * missing file nothing has been picked for, the shelf's copy of it under
 * another name, filed as another kind (`refile`, #1966's case): filing it as
 * this loader's kind is what makes it the row's likely pick.
 */
function kindOfferFor(model, canPick, file, gone, pick, answer) {
  if (!canPick) return null;
  if (file === model.filename) {
    const offer = (detail.value?.kind_offers ?? []).find(
      (entry) => entry.address === model.address,
    );
    if (offer) return offer;
  }
  const copy = answer?.refile;
  if (!gone || pick || !copy) return null;
  return {
    model_id: copy.id,
    file_kind: copy.file_kind,
    suggested_kind: model.kind,
    suggested_by: 0,
    suggested_fields: [],
    copy,
  };
}

/** What the offer of a renamed copy says in place of a workflow count. */
function copyNote(row) {
  const kind = fileKindLabel(row.kind).toLowerCase();
  return (
    `${fileName(row.offer.copy.filename)} is on your shelf as ` +
    `${fileKindLabel(row.offer.file_kind)}, and its name reads as ` +
    `${fileName(row.missing)} renamed. File it as a ${kind} and this row ` +
    "picks it for you to save."
  );
}

/**
 * Re-file a loader's model on the shelf from its Kind menu, or stop the
 * offer. A shelf write, not a workflow change: nothing joins the unsaved
 * set, and the row is read again so the offer goes once it is answered.
 */
async function fileOffered(row, changes) {
  const key = selectedKey.value;
  try {
    await editModels([row.offer.model_id], changes);
  } catch (err) {
    notices.push({
      level: "error",
      text: errorMessage(err, "Could not write that change."),
    });
    // The offer still stands, so its menu takes back the focus it held.
    await refocusRow(row.address);
    return;
  }
  const named = row.offer.copy
    ? modelLabel(fileName(row.offer.copy.filename))
    : row.savedLabel;
  // The renamed copy's offer is the row's own answer, so it is asked again.
  // Filed as this loader's kind, the copy is now its likely pick, and picked:
  // both steps in the one gesture.
  const forRow = changes.file_kind === row.kind;
  const picked = row.offer.copy ? await rereadCopy(key, row, forRow) : "";
  notices.push({
    level: "success",
    text: changes.file_kind
      ? `Filed ${named} as ${fileKindLabel(changes.file_kind)} on your shelf.` +
        (row.offer.copy && forRow
          ? picked
            ? " It is picked for this row: save to keep it."
            : " Nothing was picked for this row: choose it in the picker."
          : "")
      : changes.suggest_kind === false
        ? "The shelf will not suggest a kind for it."
        : "The shelf will suggest a kind for it again.",
  });
  // A shelf already read shows the old kind until it is read again.
  const shelf = useModelShelfStore();
  if (shelf.loaded) void shelf.fetchRows();
  if (selectedKey.value !== key) return;
  await loadDetail(key, { quiet: true });
  // The menu goes with the offer it answered; the row's own control takes
  // the focus it held rather than <body>.
  await refocusRow(row.address);
}

/**
 * Ask a missing row's offer again and, with *pick*, pick its likely match as
 * the tab does on selection. Answers with the file picked, or "".
 */
async function rereadCopy(key, row, pick) {
  const ask = `${row.kind}:${row.file}`;
  let answer;
  try {
    answer = await readModelSwap(key, {
      replacing: row.file,
      slotKind: row.kind,
      cached: true,
    });
  } catch (err) {
    console.warn(`[workflows] could not read replacements for ${row.file}`, err);
    return "";
  }
  if (selectedKey.value !== key) return "";
  replacementsByFile.value = { ...replacementsByFile.value, [ask]: answer };
  const likely = answer?.replacements?.find((candidate) => candidate.likely);
  if (!pick || !likely) return "";
  // `edit` refuses while the changes are being written or read, and says so.
  return pickModel(row, likely.filename) ? likely.filename : "";
}

/** Focus a model row's Kind menu, or its own control once the menu has gone. */
async function refocusRow(address) {
  await nextTick();
  // This tab's own Models panel: another panel on the page may draw the
  // same address.
  const panel = modelsHeading.value?.closest("section");
  const rowEl = [...(panel?.querySelectorAll("[data-address]") ?? [])].find(
    (el) => el.dataset.address === address,
  );
  rowEl
    ?.querySelector("[data-testid='kind-menu'], button, input, [tabindex='0']")
    ?.focus();
}

/**
 * The rows a model set may fill: the loader's address, the file it loads as
 * saved, its kind, and the file waiting on it. The last two let a set that
 * holds the saved file take an earlier pick back off the row.
 *
 * The file is what a set's plan names the loader by, and the plans are read
 * off the graph with the owner's replacements applied: so it is the
 * replacement on a row that holds one (`loadedFile`), never the file it
 * took the place of.
 */
const settableModels = computed(() =>
  modelRows.value
    .filter((row) => row.settable && row.file)
    .map((row) => ({
      address: row.address,
      filename: row.file,
      kind: row.kind,
      pick: row.pick,
    })),
);

/**
 * The automatic fix a missing row's pick is: the offer's likely match for the
 * file (the tab picks it itself), `{ was, now, loader }`, or null. Read off
 * the waiting pick rather than remembered, so it still says so after a reload;
 * any other pick is the owner's own, and Edited.
 */
function autoFixOf(missing, pick, candidates) {
  const likely = candidates.find((candidate) => candidate.likely);
  return missing && pick && likely?.filename === pick
    ? { was: missing, now: pick, loader: likely.loader ?? null }
    : null;
}

/** What a row's automatic fix did, in full, for its mark's tooltip. */
function autoFixNote(fix) {
  const through = fix.loader
    ? ` ComfyUI does not have it either, so a run loads it through ${fix.loader}.`
    : "";
  return (
    `${fileName(fix.was)} is not installed in ComfyUI. Your model shelf ` +
    `has ${fileName(fix.now)}, whose name reads as the same file renamed, so ` +
    `PixlStash picked it.${through} Save to keep it, or undo to put the ` +
    "original back."
  );
}

/** Pick a file for a model row; the saved one ("") takes the change back. */
function pickModel(row, file) {
  return edit((set) => withModel(set, row.address, file, row.file));
}

/**
 * *Use a set…*: every row the set answers for, as unsaved changes. A row the
 * set puts back to its saved file arrives under that file, and `withModel`
 * takes its earlier pick out of the set.
 */
function useSet({ models } = {}) {
  return edit((set) =>
    (models ?? []).reduce((next, model) => {
      const row = modelRows.value.find(
        (entry) => entry.address === model.address && entry.settable,
      );
      return row
        ? withModel(next, row.address, model.filename, row.file)
        : next;
    }, set),
  );
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
/** A LoRA to open with its loader already deleted (Save-as-recipe's hand-over). */
const editDrop = ref("");

/** Every loader of a chain read: a forked chain's lanes after its trunk. */
function loadersOf(body) {
  return [
    ...(body?.loaders ?? []),
    ...(body?.lanes ?? []).flatMap((lane) => lane.loaders ?? []),
  ];
}

/**
 * What a change names a loader by, with the shelf LoRA it holds: a strength
 * can only be set where the shelf knows the file.
 */
function slotOfLoader(loader) {
  return {
    ...slotOf(loader),
    sha256: loader.sha256 ? String(loader.sha256).toLowerCase() : "",
  };
}

/**
 * The loaders as the inspector lists them: the shelf's name, and a strength.
 * A forked chain's lanes are listed after its trunk, so every loader shows.
 */
const chainLoaders = computed(() =>
  loadersOf(chain.value).map((loader) => {
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
      slot: slotOfLoader(loader),
      savedStrength: Number.isFinite(loader.strength) ? loader.strength : null,
      // Null where the clip strength is the model strength's twin.
      savedClip:
        loader.strength_clip == null || loader.strength_clip === loader.strength
          ? null
          : loader.strength_clip,
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
async function loadChain(key, { quiet = false, asked = null } = {}) {
  const read = ++chainRead;
  // `quiet`: the rows stay as they are until the new chain is in, and
  // through a read that fails. `asked` is that read already on the wire,
  // where the caller needs its answer too.
  if (!quiet) {
    chain.value = null;
    chainFailed.value = false;
    chainNoGraph.value = false;
  }
  if (!key) {
    chainPending.value = false;
    return;
  }
  chainPending.value = !quiet;
  try {
    const body = await (asked ?? getLoraChain(key));
    if (read !== chainRead) return;
    chain.value = body;
    chainFailed.value = false;
    chainNoGraph.value = false;
  } catch (err) {
    if (read !== chainRead) return;
    // No graph is an answer, and is drawn; anything else is a failure.
    const noGraph = err?.response?.status === 409;
    if (!noGraph) {
      console.warn(`[workflows] could not read the LoRA chain of ${key}`, err);
      if (quiet) return;
    }
    chain.value = null;
    chainNoGraph.value = noGraph;
    chainFailed.value = !noGraph;
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

/**
 * Whether the parameter rows are waiting for their choices. A row is a plain
 * box until ComfyUI's list for it is in, and a box that turns into a
 * drop-down after it was drawn is worse than a row drawn a moment later. Only
 * for `FORM_HOLD_MS`: a ComfyUI that is slow to answer leaves boxes, which
 * still set the value.
 */
const formHeld = ref(false);
const FORM_HOLD_MS = 1000;
let formHold = 0;
function holdForm(held) {
  clearTimeout(formHold);
  formHeld.value = held;
  if (held) {
    formHold = setTimeout(() => {
      formHeld.value = false;
    }, FORM_HOLD_MS);
  }
}
onBeforeUnmount(() => clearTimeout(formHold));

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
async function loadFormInputs(key, { quiet = false } = {}) {
  const read = ++formRead;
  // `quiet`: the choices stay as they are until the new ones are in, and a
  // failure leaves them. Blanking them swaps every list for a plain box and
  // back, which drops focus from the one being used.
  if (!quiet) {
    formNodes.value = [];
    formFailed.value = "";
    holdForm(Boolean(key));
  }
  if (!key) {
    formPending.value = false;
    return;
  }
  if (!quiet) formPending.value = true;
  try {
    const body = await getWorkflowFormInputs(key);
    if (read !== formRead) return;
    formNodes.value = body.nodes ?? [];
    formFailed.value = "";
  } catch (err) {
    if (read !== formRead) return;
    console.warn(`[workflows] could not read the form inputs of ${key}`, err);
    if (quiet) return;
    formFailed.value =
      err?.response?.status === 409
        ? "PixlStash has no graph for this workflow, so it has no nodes to pick from."
        : errorMessage(err, "Could not read this workflow's nodes just now.");
  } finally {
    if (read === formRead) {
      formPending.value = false;
      holdForm(false);
    }
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
      // Only a default the graph has a loader for can be changed: a change
      // names a loader, and this one's strength is the default's.
      slot: loader?.slot ?? null,
      savedStrength: Number.isFinite(lora.strength) ? lora.strength : null,
      savedClip: loader?.savedClip ?? null,
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
  (usingChain.value ? chainLoaders.value : defaultLoras.value).map((row) => {
    const slot = row.slot;
    const waitingStrength = slot ? loraStrength(waiting.value, slot) : null;
    return {
      ...row,
      slotKey: slot ? `${slot.node_id}:${slot.field}` : "",
      removed: slot ? loraRemoved(waiting.value, slot) : false,
      strengthValue: formatStrength(waitingStrength ?? row.savedStrength),
      strengthEdited: waitingStrength !== null,
      savedStrengthText: formatStrength(row.savedStrength) || "—",
      canStrength: Boolean(slot?.sha256) && row.savedStrength !== null,
    };
  }),
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
    saved: Boolean(on),
    on: stageOn(waiting.value, key, on),
    edited: stageOn(waiting.value, key, on) !== Boolean(on),
    detail: defaultRecipe.value?.stage_details?.[key] || "",
    label:
      STAGE_LABELS[key] ??
      key.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase()),
  })),
);

/** "Off: most of its pictures ran without it": of a vote, not of the owner's
 *  own answer, so only for a stage that is off as saved and untouched. */
const stageVoteNote = computed(() =>
  stageRows.value.some((stage) => !stage.saved && !stage.edited),
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
  editDrop.value = drop;
  editKey.value = key;
}

const editLorasButton = ref(null);

/**
 * The dialog is gone, however it ended (Cancel, Apply, Save), and took focus
 * with it: nothing restores focus after an overlay opened from code. It goes
 * back to *Edit LoRAs…*, unless it has already landed somewhere of its own.
 */
async function closeEditLoras() {
  editKey.value = "";
  editDrop.value = "";
  await nextTick();
  const active = document.activeElement;
  if (!active || active === document.body) editLorasButton.value?.focus?.();
}

// The name and count arrive with the card when the dialog was opened from a
// link before the grid or the detail read had landed.
watch(card, (next) => {
  if (!editKey.value || next?.id !== editKey.value) return;
  if (!editName.value) editName.value = next.name || "";
  if (!editPictures.value) editPictures.value = Number(next.picture_count) || 0;
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
async function loadDetail(key, { quiet = false } = {}) {
  const read = ++detailRead;
  // `quiet` (the graph moved under a rail already drawn): nothing is blanked
  // while the read is out, and a failure leaves what was there.
  if (!quiet) {
    detail.value = null;
    detailFailed.value = false;
  }
  if (!key) {
    detailPending.value = false;
    return;
  }
  detailPending.value = !quiet;
  try {
    const body = await getWorkflowCard(key);
    // Another card may have been selected while this was on the wire, or a
    // newer answer (the Recipes tab's write) already landed.
    if (selectedKey.value !== key || read !== detailRead) return;
    const before = detail.value;
    detail.value = body;
    detailFailed.value = false;
    // The parameter rows keep their groups on a quiet read.
    if (quiet && before) return;
    openedPins.value = body.pins ?? null;
  } catch (err) {
    console.warn(`[workflows] could not read the card ${key}`, err);
    if (selectedKey.value !== key) return;
    if (!quiet) detailFailed.value = true;
  } finally {
    if (selectedKey.value === key) detailPending.value = false;
  }
}

// ── The unsaved changes (#1850) ────────────────────────────────────────────
//
// A change to the graph (a model, a stage, a LoRA loader or its strength) is
// not written when it is made: it joins ONE set of changes the server keeps
// for the workflow, and the bar above Run… saves the set. The rail is a view
// of that set, so it survives another card being selected, the inspector
// closing and a restart, and nothing here asks "discard changes?".

/**
 * `GET …/changes` for the shown workflow, with the workflow it is of:
 * `{ key, state }`. `state` carries what waits, how many, and which of the
 * two verbs are open and why not (the server's sentences, printed as sent).
 */
const changesRead = ref(null);
/** The read failed: the rows are locked, and the rail says so with a Retry. */
const changesFailed = ref(false);
/**
 * Which read is the latest. Every ask is for the workflow on screen, and a
 * write's answer counts as one only when it is that workflow's too: an answer
 * for the workflow just left must never retire the read of the one arrived at.
 */
let changesAsk = 0;
/** The newest read of what waits: the one out, or the last to come back. */
let changesReading = Promise.resolve();

/**
 * The set as the last edit left it, drawn until the server answers:
 * `{ key, changes }`, or null once it has.
 */
const intent = ref(null);
/** Per workflow, the newest set not yet sent: an edit made while one is out. */
const unsent = new Map();
/** One write of the set at a time; the newest intent is what the next sends. */
let changeWrites = Promise.resolve();

/** The server's sentence for a save it refused, or "". */
const draftError = ref("");
/** *Save as new…* is asking for its name. */
const naming = ref(false);
const newName = ref("");
const nameField = ref(null);

/** The read, when it is of the workflow on screen: never another's. */
const shownChanges = computed(() =>
  changesRead.value?.key === selectedKey.value ? changesRead.value.state : null,
);

/** What waits on the shown workflow, as every row reads it. */
const waiting = computed(() =>
  intent.value?.key === selectedKey.value
    ? intent.value.changes
    : whole(shownChanges.value?.waiting),
);

/** How many: the server's count, or the rows' own while an edit is out. */
const changeCount = computed(() =>
  intent.value?.key === selectedKey.value
    ? countChanges(intent.value.changes)
    : Number(shownChanges.value?.count) || 0,
);

/**
 * What is settling each workflow's set, from the press until its answer is
 * drawn, as `{ what, fromBar }`: `save`, `save-as-new`, `discard`, a go-back
 * to another `version`, the `rebase` after Edit LoRAs… saved, or an `apply`
 * of that dialog's made off the rows. `fromBar` says the press was made with
 * focus in the bar, whose verbs it disables (see the watcher on `barOf`).
 *
 * **The one rule for who may touch a set.** While something settles it,
 * nothing else of it is made or sent: no row edits it and no verb is pressed.
 * An edit whose write is still out excludes nothing. The next edit queues
 * behind it, and so does whatever settles the set (`settle`), which is why a
 * strength typed and Save clicked in one gesture both land.
 */
const settling = reactive(new Map());
/** The same for the workflow on screen, or "". */
const settlingShown = computed(
  () => settling.get(selectedKey.value)?.what ?? "",
);

/**
 * The workflow whose rows off the graph are being read again (`refreshGraph`),
 * or "". Until they are in, each still says the saved value of a graph that
 * is gone, and an edit is measured against that value.
 */
const rereading = ref("");

/**
 * Whether a row may edit the shown workflow's set: it has been read, since
 * the next set is built from what waits, nothing is settling it, and the rows
 * are not being read again.
 */
const canEdit = computed(
  () =>
    Boolean(shownChanges.value) &&
    !settlingShown.value &&
    rereading.value !== selectedKey.value,
);

// A lock disables whichever row control has focus, and a browser then drops
// that focus on the page. It is given back when the lock lifts, unless the
// owner has put it somewhere else meanwhile. A strength half typed is not
// kept across the lock: the field is drawn again with what the row holds,
// since the number was typed for a row that was about to change.
let heldFocus = null;
watch(canEdit, async (can) => {
  if (!can) {
    heldFocus = document.activeElement;
    return;
  }
  const held = heldFocus;
  heldFocus = null;
  await nextTick();
  const active = document.activeElement;
  if (
    held?.isConnected &&
    !held.disabled &&
    (!active || active === document.body || active === held)
  ) {
    held.focus();
  }
});

/**
 * What waits was made on a version the workflow has since left (`stale`):
 * Save is closed for it, and Open in ComfyUI opens the graph without it.
 */
const changesStale = computed(() => Boolean(shownChanges.value?.stale));

/**
 * An edit's write is out for the shown workflow, so the read is of the set
 * BEFORE it while the bar counts the one after. Until the answer for that
 * set is in, the bar says nothing the read knew only of the other:
 *
 * - A verb the bar already drew stays drawn. It may be the control the edit's
 *   own gesture is about to click (a strength typed, then Save), and a verb
 *   taken away under the pointer swallows that click. Pressed, it queues
 *   behind the write, and the server answers for the set as it then is.
 * - A bar that was not up (the read's set was empty) draws no verb yet.
 * - No reason is printed, but that of a stale set: editing it again does not
 *   end that, so it is as true of the set being counted.
 */
const editOut = computed(() => intent.value?.key === selectedKey.value);
const verbsRead = computed(
  () => !editOut.value || Number(shownChanges.value?.count) > 0,
);

const saveOpen = computed(
  () => verbsRead.value && Boolean(shownChanges.value?.save?.open),
);
const saveAsNewOpen = computed(
  () => verbsRead.value && Boolean(shownChanges.value?.save_as_new?.open),
);

/** Why a verb is not offered: the server's sentences, once each. */
const closedReasons = computed(() =>
  editOut.value && !changesStale.value
    ? []
    : [
        ...new Set(
          [shownChanges.value?.save, shownChanges.value?.save_as_new]
            .filter((verb) => verb && !verb.open && verb.reason)
            .map((verb) => verb.reason),
        ),
      ],
);

/**
 * Read what waits on the shown workflow.
 *
 * **Awaited, it ends when what waits is drawn, whoever read it.** A read that
 * a newer one overtook draws nothing, so it waits for that one instead. The
 * grid's card starts such a read the moment a save here empties the set, and
 * whatever follows an `await loadChanges(…)` would otherwise run with the
 * old state still on screen. A read that a write's answer retired has nothing
 * to wait for, since the answer is drawn already; nor has a read for a
 * workflow the rail has left.
 */
async function loadChanges(key) {
  if (!stillOn(key)) return undefined;
  const ask = ++changesAsk;
  changesFailed.value = false;
  if (!key) {
    changesRead.value = null;
    return undefined;
  }
  const turn = (async () => {
    let state;
    let failed = false;
    try {
      state = await getWorkflowChanges(key);
    } catch (err) {
      console.warn(`[workflows] could not read the changes of ${key}`, err);
      failed = true;
    }
    // Another workflow may have been selected while this was on the wire.
    if (!stillOn(key)) return undefined;
    // Overtaken: by a newer read, or by a write's answer (`takeChanges`),
    // which starts no read. A turn must not wait for itself.
    if (ask !== changesAsk) {
      return changesReading === turn ? undefined : changesReading;
    }
    changesRead.value = failed ? null : { key, state };
    changesFailed.value = failed;
    return undefined;
  })();
  changesReading = turn;
  return turn;
}

/**
 * Retry takes its own button away once the read is in, so focus goes to the
 * heading of the rows it unlocked, never to the page.
 */
const modelsHeading = ref(null);
async function retryChanges() {
  await loadChanges(selectedKey.value);
  await nextTick();
  const active = document.activeElement;
  if (!active || active === document.body) modelsHeading.value?.focus();
}

/**
 * A write's answer is the newest state there is of its workflow: on screen,
 * it retires any read out. For a workflow the rail has left it is dropped,
 * and the ask is left alone: the read out is the shown workflow's own, and
 * coming back to this one reads it again.
 */
function takeChanges(key, state) {
  if (intent.value?.key === key) intent.value = null;
  if (!stillOn(key)) return;
  changesAsk += 1;
  changesFailed.value = false;
  changesRead.value = { key, state };
}

/**
 * Make one edit: build the next set from what waits, draw it, and store it.
 *
 * Refused where a row could not have made it (`canEdit`): every control is
 * disabled then, so this is for whatever comes past them, a menu left open or
 * a dialog's answer. Said, because the change is otherwise lost in silence.
 *
 * **One write on the wire, the last intent wins.** Each edit queues a link;
 * a link sends the newest set there is when its turn comes, and an answer is
 * drawn only when no newer set is waiting behind it, so the first of two
 * quick edits can never draw over the second.
 */
function edit(build) {
  const key = selectedKey.value;
  if (!key) return undefined;
  if (!canEdit.value) {
    notices.push({
      level: "error",
      text: settling.has(key)
        ? "That change was not kept: this workflow's changes were being written. Make it again."
        : "That change was not kept: this workflow's unsaved changes could not be read. Try again.",
    });
    if (changesFailed.value) void loadChanges(key);
    return undefined;
  }
  draftError.value = "";
  const next = build(waiting.value);
  intent.value = { key, changes: next };
  unsent.set(key, next);
  changeWrites = changeWrites.then(async () => {
    const set = unsent.get(key);
    if (!set) return;
    unsent.delete(key);
    try {
      const state = await setWorkflowChanges(key, set);
      if (unsent.has(key)) return;
      takeChanges(key, state);
    } catch (err) {
      if (unsent.has(key)) return;
      if (intent.value?.key === key) intent.value = null;
      fail(err, "Could not keep that change.");
      if (stillOn(key)) void loadChanges(key);
      return;
    }
    // This tab's own write is not echoed back to it, so the card's Unsaved
    // badge is asked for here.
    void store.refetch();
  });
  return changeWrites;
}

/** Switch a stage on or off. */
function toggleStage(stage, on) {
  return edit((set) => withStage(set, stage.key, on, stage.saved));
}

/** The LoRA panel: where a removed row's *Put back* and × are found. */
const loraPanel = ref(null);

/**
 * Remove a loader, or put it back. The control pressed leaves with the row's
 * state, so focus goes to the one that took its place: *Put back* after ×,
 * × after *Put back*.
 */
async function setLoraRemoved(lora, removed) {
  if (!lora.slot) return;
  const done = edit((set) => withLoraRemoved(set, lora.slot, removed));
  await nextTick();
  const row = [...(loraPanel.value?.querySelectorAll("[data-slot]") ?? [])].find(
    (entry) => entry.dataset.slot === lora.slotKey,
  );
  row
    ?.querySelector(
      `[data-testid="${removed ? "wftab-lora-put-back" : "wftab-lora-remove"}"]`,
    )
    ?.focus();
  await done;
}

/**
 * A strength typed on a row, on change (blur or Enter), never per keystroke.
 * Anything that is not a strength the server takes puts the field back.
 */
function setLoraStrength(lora, raw, field = null) {
  const strength = raw === "" ? NaN : Number(raw);
  if (!lora.slot || !Number.isFinite(strength) || Math.abs(strength) > 10) {
    if (field) field.value = lora.strengthValue;
    return undefined;
  }
  return edit((set) =>
    withLoraStrength(set, lora.slot, strength, {
      strength: lora.savedStrength,
      strength_clip: lora.savedClip,
    }),
  );
}

/** The workflow whose bar of unsaved changes is drawn, or "". */
const barOf = computed(() =>
  tab.value === "workflow" && card.value && !multiple.value && changeCount.value
    ? selectedKey.value
    : "",
);
const draftBar = ref(null);

/** The bar's count, in words: what it prints and what is announced. */
function unsavedText(count) {
  return `${count} unsaved ${count === 1 ? "change" : "changes"}`;
}

/** How many of the waiting changes are the tab's own fixes, still unchanged. */
const autoFixCount = computed(
  () => modelRows.value.filter((row) => row.fix).length,
);

/** The bar's count: automatic fixes said as such, the rest as changes. */
function draftText(count) {
  const fixed = Math.min(autoFixCount.value, count);
  if (!fixed) return unsavedText(count);
  const fixes = `${fixed} automatic ${fixed === 1 ? "fix" : "fixes"}`;
  return count > fixed ? `${fixes} · ${unsavedText(count - fixed)}` : fixes;
}

/**
 * The bar has just arrived, in two parts that end at different times.
 * `arrived` is the landing pulse, and lasts as long as the bar. `arrival` is
 * what the live region says, the count as the first change left it: emptied
 * the moment the count moves and never filled again while the bar is up, so
 * a count that comes back to the same number (one, two, one) is not said
 * twice.
 *
 * Only a change made to a set already read as empty is an arrival, wherever
 * it was made: here, or in another window while this workflow is on screen.
 * Selecting a workflow that has changes waiting takes the same count from
 * nothing to three, and that is the rail being read, not a change being
 * made. It follows the bar, which is drawn before the change's write has
 * answered. Gone with the bar, so coming back to this tab does not play it
 * again.
 */
const arrival = ref("");
const arrived = ref(false);
watch(
  () => [selectedKey.value, Boolean(shownChanges.value), changeCount.value],
  ([key, , count], [wasKey, wasRead, wasCount] = []) => {
    if (count && !wasCount && wasRead && key === wasKey && barOf.value) {
      arrival.value = draftText(count);
      arrived.value = true;
    } else if (count !== wasCount) {
      arrival.value = "";
    }
  },
);

function focusInBar() {
  return Boolean(draftBar.value?.contains(document.activeElement));
}

// The bar leaves with whatever in it held focus, and a browser then drops
// that focus on the page. So when the bar goes, focus goes to Run…, the next
// control down, whatever took the bar away: a save or a discard here, a save
// made elsewhere, the selection moving to the workflow Save as new made.
//
// Only when focus was the bar's to lose: it is in the bar now (read before
// the bar is taken out), or the press that is settling the set was made from
// it. A pressed verb is disabled until its answer is drawn, which drops focus
// just as a removal does, so `settle` notes where the press came from. Never
// when the owner has put focus somewhere else meanwhile.
//
// No verb moves focus itself. A button that gets focus back when its loading
// ends (`AppButton`), or from the rows' lock (the watcher on `canEdit`), is
// in the bar again, and is covered when the bar then goes.
watch(barOf, async (now, was) => {
  if (was) {
    arrival.value = "";
    arrived.value = false;
  }
  if (now || !was) return;
  if (!focusInBar() && !settling.get(was)?.fromBar) return;
  await nextTick();
  const active = document.activeElement;
  if (!active || active === document.body) runButton.value?.focus();
});

/** A finished save's words: the version it made, and what it left out. */
function savedText(saved, lead) {
  const left = saved?.left_out?.length ?? 0;
  return left
    ? `${lead} ${left} loader${left === 1 ? "" : "s"} holding a person's LoRA ${
        left === 1 ? "was" : "were"
      } left out: a person is added to a run, not saved in a workflow.`
    : lead;
}

/**
 * Settle the set of `key`: run `work` as the one thing writing it.
 *
 * Marked at the press, not when its turn comes, so no edit can be made in
 * between and what the caller read at the press is what `work` acts on. The
 * edits made before the press go first: their writes are the queue as it
 * stood then (`changeWrites`), and none can join it after. Queued with every
 * other write, so two things settling one set never run beside each other.
 */
function settle(key, what, work) {
  const mark = { what, fromBar: stillOn(key) && focusInBar() };
  settling.set(key, mark);
  const sent = changeWrites;
  return queueWrite(what, async () => {
    try {
      await sent;
      await work();
    } finally {
      // Only its own mark: one made since is of a turn still to come.
      if (toRaw(settling.get(key)) === mark) settling.delete(key);
    }
  });
}

/**
 * Save: what waits, written over the workflow as its next version. A refusal
 * (409) is printed in the bar in the server's words, and the verbs are read
 * again since they are what changed.
 */
function saveChanges() {
  const key = selectedKey.value;
  if (!key || !saveOpen.value || settling.has(key)) return undefined;
  draftError.value = "";
  // Read at the press, for the workflow pressed on: when the save's turn
  // comes the rail may show another, whose version this is not. Null is a
  // version too (a workflow never saved over), and the server checks it.
  const version = shownChanges.value.version ?? null;
  return settle(key, "save", async () => {
    let saved;
    try {
      saved = await saveWorkflow(key, { version });
    } catch (err) {
      console.warn(`[workflows] could not save the changes of ${key}`, err);
      if (!stillOn(key)) {
        fail(err, "Could not save those changes.");
        return;
      }
      draftError.value = errorMessage(err, "Could not save those changes.");
      void loadChanges(key);
      return;
    }
    void store.refetch();
    notices.push({
      level: "success",
      text: savedText(
        saved,
        saved?.version != null && saved.version !== version
          ? `Saved as version ${saved.version}. The version menu takes it back.`
          : "Saved.",
      ),
    });
    if (!stillOn(key)) return;
    // Started with the save's answer, before the grid's card can say the
    // version moved; the rows are locked until it is in (`rereading`). The
    // set stays locked until both are drawn, so nothing is handed focus in a
    // bar that is about to go.
    await Promise.all([refreshGraph(key), loadChanges(key)]);
  });
}

function startNaming() {
  naming.value = true;
  draftError.value = "";
  nextTick(() => nameField.value?.focus());
}

/** *Back* from the name question: the two verbs again, focus on the one left. */
async function stopNaming() {
  naming.value = false;
  await nextTick();
  saveAsNewButton.value?.focus?.();
}
const saveAsNewButton = ref(null);

/**
 * Save as new…: the same graph stored as a new workflow under the name
 * given. This one is left as it was, with nothing waiting, and the new one
 * is selected.
 */
function saveAsNew() {
  const key = selectedKey.value;
  const name = newName.value.trim();
  // Enter in the name field comes here past the disabled button: a second
  // one while the save is out would queue the same new workflow again.
  if (!key || !name || !saveAsNewOpen.value || settling.has(key)) {
    return undefined;
  }
  draftError.value = "";
  const unmoved = selectionMark();
  return settle(key, "save-as-new", async () => {
    let saved;
    try {
      saved = await saveWorkflowAsNew(key, { name });
    } catch (err) {
      console.warn(`[workflows] could not save ${key} as a new workflow`, err);
      if (!stillOn(key)) {
        fail(err, "Could not save that as a new workflow.");
        return;
      }
      draftError.value = errorMessage(
        err,
        "Could not save that as a new workflow.",
      );
      void loadChanges(key);
      return;
    }
    naming.value = false;
    newName.value = "";
    notices.push({
      level: "success",
      text: savedText(saved, `Saved as a new workflow, ${saved?.name || name}.`),
    });
    await store.refetch();
    if (!unmoved()) return;
    if (saved?.workflow_id) store.select(saved.workflow_id);
    else await loadChanges(key);
  });
}

/** Discard what waits. Nothing is asked: the saved version is untouched. */
function discardChanges() {
  const key = selectedKey.value;
  if (!key || settling.has(key)) return undefined;
  draftError.value = "";
  naming.value = false;
  unsent.delete(key);
  return settle(key, "discard", async () => {
    try {
      takeChanges(key, await discardWorkflowChanges(key));
    } catch (err) {
      // An edit not yet sent was dropped at the press: the rows go back to
      // what the server holds, which the read below asks for.
      if (intent.value?.key === key) intent.value = null;
      fail(err, "Could not discard those changes.");
      if (stillOn(key)) void loadChanges(key);
      return;
    }
    void store.refetch();
  });
}

/**
 * Which version of which workflow the rows off the graph were last read at,
 * and the read of them that is out. Whatever this tab does that moves the
 * graph reads it again at once; the grid's card says so a moment later, and
 * with these two it reads nothing twice, whichever of them moved it.
 */
let graphAt = "";
let refreshing = Promise.resolve();

/**
 * Read again everything the rail reads off the graph, in place: nothing is
 * blanked, and the rows keep their groups.
 *
 * Only for the workflow on screen. Each of these reads keeps the newest ask,
 * so one made for a workflow the rail has left would retire the reads of the
 * one it shows now.
 */
function refreshGraph(key, { chain: asked = null } = {}) {
  if (!stillOn(key)) return Promise.resolve();
  rereading.value = key;
  const turn = (async () => {
    try {
      await Promise.all([
        loadDetail(key, { quiet: true }),
        loadChain(key, { quiet: true, asked }),
        loadFormInputs(key, { quiet: true }),
      ]);
    } finally {
      // The newest read's to lift: an earlier one ends with rows still out.
      if (refreshing === turn) rereading.value = "";
    }
    if (!stillOn(key)) return;
    graphAt = `${key}:${detail.value?.card?.version ?? ""}`;
    void checkInstalled(key, { quiet: true });
  })();
  refreshing = turn;
  return turn;
}

/**
 * The card says the graph is at `version`: read it, unless that is read. A
 * read of these rows already out may be of that very version, so it is
 * waited for first; the rows are locked by it meanwhile. With none out the
 * read starts here and now, so the rows are never editable in between.
 */
async function followGraph(key, version) {
  if (rereading.value === key) await refreshing;
  if (graphAt !== `${key}:${version}`) void refreshGraph(key);
}

// The shown workflow's graph or its waiting changes moved, whoever moved
// them: a save from the Run popup or the Tasks tab, ComfyUI, an assistant,
// another window. The grid's card says so (`version`, `unsaved_changes`), and
// the rows and the bar follow it without the rail going blank. A selection
// change is not this: the watcher on the selection reads everything.
watch(
  () => [
    selectedKey.value,
    card.value?.version,
    card.value?.unsaved_changes,
  ],
  ([key, version, unsaved], [oldKey, oldVersion] = []) => {
    if (!key || key !== oldKey) return;
    const moved =
      version !== oldVersion && version != null && oldVersion != null;
    if (moved) void followGraph(key, version);
    // An edit still out answers with the newest state itself.
    if (intent.value?.key === key) return;
    const state = shownChanges.value;
    if (
      state &&
      (Number(state.count) || 0) === (Number(unsaved) || 0) &&
      (!moved || state.version === version)
    ) {
      return;
    }
    void loadChanges(key);
  },
);

// The Run popup or the Tasks tab saved this workflow's changes, in this
// window. The server does not announce a write to the window that made it, so
// the card may never say so: the popup does (`runDialog.saved`). Whatever
// waited here is saved or cleared by now, so it is dropped and read again,
// with the head and the rows off the graph, in place.
watch(
  () => runDialog.saved,
  (saved) => {
    const key = selectedKey.value;
    if (!saved || !key || saved.workflowId !== key) return;
    unsent.delete(key);
    if (intent.value?.key === key) intent.value = null;
    draftError.value = "";
    naming.value = false;
    void loadChanges(key);
    void loadVersions(key);
    void refreshGraph(key);
  },
);

/**
 * Edit LoRAs… ended in Apply: removals and strengths, which can wait. They
 * join the set of the workflow the dialog was opened on.
 *
 * On the rows where they can take it, as one more edit. Otherwise nothing
 * here holds that set to build on (the rail shows another workflow, the read
 * is out, or a save is): it is read when the write's turn comes, behind
 * whatever was writing it, so the answer is never dropped and never laid over
 * a set that has moved.
 */
function applyLoraEdit(fragment) {
  const key = editKey.value;
  if (!key) return undefined;
  if (stillOn(key) && canEdit.value) {
    return edit((set) => withFragment(set, fragment));
  }
  return settle(key, "apply", async () => {
    try {
      const state = await getWorkflowChanges(key);
      takeChanges(
        key,
        await setWorkflowChanges(key, withFragment(state?.waiting, fragment)),
      );
      void store.refetch();
    } catch (err) {
      fail(err, "Could not keep that LoRA edit.");
    }
  });
}

/**
 * Edit LoRAs… ended in Save: a loader moved or added, which cannot wait and
 * was written as the workflow's next version.
 *
 * What was waiting was made against the version before, and the server
 * refuses to save a set made against another version. It is the same set of
 * rows, though, and a chain edit keeps its loaders' ids: so it is stored
 * again against the new version, less any loader the edit took out.
 *
 * It settles the set as a save does: behind every write of it that was out,
 * with the rows locked until it is drawn. It is about `key` and nothing the
 * rail shows: the set and the new version's chain are both read from the
 * server, by id, when its turn comes, so an edit whose write was still out
 * when the dialog saved is in it, and a rail that has moved on changes
 * nothing. Discarded first, then stored: a set that is replaced keeps the
 * version it was made against.
 */
function onOverwritten(key) {
  // What the bar offered when the dialog saved, before the version moved.
  // Not known for a workflow the rail has left, and then nothing is said.
  const couldSave = stillOn(key) && saveOpen.value;
  return settle(key, "rebase", async () => {
    void store.refetch();
    // Where the rail shows this workflow it reads the chain again anyway:
    // that one read serves the trim below too. Off screen the chain is read
    // only once something is known to wait.
    const asked = stillOn(key) ? getLoraChain(key) : null;
    if (asked) void refreshGraph(key, { chain: asked });
    let waits;
    try {
      waits = await getWorkflowChanges(key);
    } catch (err) {
      console.warn(`[workflows] could not read the changes of ${key}`, err);
      void loadChanges(key);
      return;
    }
    if (!countChanges(waits?.waiting)) {
      takeChanges(key, waits);
      return;
    }
    let loaders;
    try {
      loaders = loadersOf(await (asked ?? getLoraChain(key)));
    } catch (err) {
      // Without the chain there is no telling which loaders the edit took
      // out. Keeping every LoRA change may name one that is gone, dropping
      // them loses ones that are not: so the set is left as it is, and said.
      console.warn(`[workflows] could not read the LoRA chain of ${key}`, err);
      notices.push({
        level: "error",
        text: "The LoRA edit was saved. The changes that were waiting are left as they were: its LoRAs could not be read to store them against the new version.",
      });
      takeChanges(key, waits);
      return;
    }
    const kept = withoutGoneLoaders(waits.waiting, loaders.map(slotOfLoader));
    try {
      await discardWorkflowChanges(key);
      const state = await setWorkflowChanges(key, kept);
      takeChanges(key, state);
      if (couldSave && countChanges(kept) && !state?.save?.open) {
        notices.push({
          level: "error",
          text:
            state?.save?.reason ||
            "The changes that were waiting cannot be saved over the new version.",
        });
      }
    } catch (err) {
      fail(
        err,
        "The LoRA edit was saved, but the changes that were waiting could not be kept.",
      );
      void loadChanges(key);
    }
  });
}

// *Use a set…* asked for from outside the rail (the selection bar): the
// workflow is selected, the rail opened on this tab, and the menu opened once
// the model rows it fills are in and can be edited.
//
// The wish is for one workflow at one moment. It is taken back when the
// selection moves on, or when the rows are in and it cannot be met; kept, it
// would open the menu unasked the next time that workflow was selected.
let useSetWanted = "";
watch(
  () => store.useSetKey,
  (key) => {
    if (!key) return;
    store.requestUseSet("");
    useSetWanted = key;
    if (selectedKey.value !== key) store.select(key);
    tab.value = "workflow";
    sidebarStore.openWorkflowInspector();
    void openWantedSet();
  },
  { immediate: true },
);
async function openWantedSet() {
  const key = useSetWanted;
  if (!key) return;
  if (!stillOn(key)) {
    useSetWanted = "";
    return;
  }
  // The rows are this workflow's once its detail is: until then they are
  // the last one's, or none.
  if (detail.value?.card?.id !== key) return;
  if (!settableModels.value.length) {
    useSetWanted = "";
    // Asked for from the grid, where nothing says why nothing opened.
    notices.push({
      level: "info",
      text: "This workflow has no model a set could fill.",
    });
    return;
  }
  // No graph, or changes that could not be read: the rail says which.
  if (chainNoGraph.value || changesFailed.value) {
    useSetWanted = "";
    return;
  }
  // The changes are still being read, or the set is being saved: soon.
  if (!canEdit.value) return;
  useSetWanted = "";
  await nextTick();
  useSetMenu.value?.open?.();
}
watch(
  () => [
    selectedKey.value,
    detail.value,
    settableModels.value.length,
    chainNoGraph.value,
    changesFailed.value,
    canEdit.value,
  ],
  openWantedSet,
);
const useSetMenu = ref(null);

function fail(err, fallback) {
  console.warn("[workflows] a workflow write failed", err);
  notices.push({ level: "error", text: errorMessage(err, fallback) });
}

/** A versions answer as the rail keeps it, tied to the workflow it is of. */
function versionsOf(key, body) {
  return {
    key,
    version: body?.version ?? null,
    versions: Array.isArray(body?.versions) ? body.versions : [],
  };
}

/** Read the versions of one workflow; none is asked for one with no history. */
async function loadVersions(key) {
  const ask = ++versionsAsk;
  versionsFailed.value = false;
  if (!key || !(card.value?.versions > 1)) {
    versionsRead.value = null;
    return;
  }
  try {
    const body = await getWorkflowVersions(key);
    // Another workflow may have been selected while this was on the wire, or
    // a go-back's answer, which is newer, already landed.
    if (ask !== versionsAsk || !stillOn(key)) return;
    versionsRead.value = versionsOf(key, body);
  } catch (err) {
    console.warn(`[workflows] could not read the versions of ${key}`, err);
    if (ask !== versionsAsk || !stillOn(key)) return;
    // The head falls back to the card's number; the menu says the read failed.
    versionsRead.value = null;
    versionsFailed.value = true;
  }
}

/** The menu opened with no list to show: ask again rather than sit failed. */
function reopenVersions() {
  if (!shownVersions.value) void loadVersions(selectedKey.value);
}

// Read again whenever the shown workflow's version moves, which the grid's
// card says: a save from the Run popup, Edit LoRAs…, ComfyUI or an assistant
// is announced (`workflows_changed`), the grid is re-read, and the head and
// the menu follow it here.
watch(
  () => [
    selectedKey.value,
    card.value?.versions > 1 ? card.value.version : null,
  ],
  ([key, number]) => {
    // A go-back's answer is already the list at this number.
    if (number !== null && shownVersions.value?.version === number) return;
    void loadVersions(key);
  },
  { immediate: true },
);

/**
 * Go back: the chosen version's graph becomes the newest version. Nothing is
 * removed, so it is undone by going back again, and nothing is asked first.
 *
 * Changes waiting on the workflow do not stop it: the server then refuses
 * Save on them, in its own sentence. It settles the set all the same
 * (`settle`): every row is about to say another graph's saved value, so none
 * is edited until that graph has been read.
 */
function goBack(row, key = selectedKey.value) {
  if (!key) return;
  // `key` is not always the workflow on screen: a notice's Undo outlives the
  // selection it was made on. Nothing here reads or clears the rail for it
  // unless the rail still shows it.
  if (stillOn(key)) versionError.value = "";
  return settle(key, "version", async () => {
    let body;
    try {
      body = await restoreWorkflowVersion(key, row.version);
    } catch (err) {
      const fallback = `Could not go back to version ${row.version}.`;
      if (!stillOn(key)) {
        // The rail shows another workflow by now: said as a notice instead.
        fail(err, fallback);
        return;
      }
      console.warn(
        `[workflows] could not go back to version ${row.version} of ${key}`,
        err,
      );
      versionError.value = errorMessage(err, fallback);
      // A refusal (no such version, already on it) means the list is stale.
      void loadVersions(key);
      return;
    }
    // The grid's card carries the version too. Asked here as well as by the
    // announcement, which a dropped socket would lose.
    void store.refetch();
    // The version left, for the notice's Undo: going back to it again. Read
    // off the answer, where it is the row under the one this made, so it is
    // this workflow's whatever the rail shows by now. An answer that does
    // not say offers no Undo rather than a wrong one.
    const versions = Array.isArray(body?.versions) ? body.versions : [];
    const made = versions.findIndex((entry) => entry.current);
    const left = made < 0 ? undefined : versions[made + 1]?.version;
    // Owed even when the selection has moved on: it was written either way,
    // and a number changing in the head is not much of a receipt.
    notices.push({
      level: "success",
      timeout: 8000,
      text: `Back on version ${row.version}, kept as version ${body?.version}. Nothing was removed.`,
      ...(left && left !== row.version
        ? {
            action: {
              label: "Undo",
              handler: () => goBack({ version: left }, key),
            },
          }
        : {}),
    });
    if (!stillOn(key)) return;
    versionsAsk += 1;
    versionsFailed.value = false;
    versionsRead.value = versionsOf(key, body);
    // Everything else the rail reads off the graph is another graph's now,
    // and what was waiting was made against the version left.
    void loadChanges(key);
    await refreshGraph(key);
  });
}

/** Dismiss takes its own button away, so focus goes back to the version chip. */
async function dismissVersionError() {
  versionError.value = "";
  await nextTick();
  versionMenu.value?.focus();
}

/**
 * Run a write after whichever is already out, never beside it.
 *
 * `defaults`, `pins` and the card all come back on one `detail`, so two
 * writes in flight means the slower answer discards the faster one's change.
 * **Queued rather than refused**, so a second click made while the first
 * write is out is not dropped, leaving its control looking dead.
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
 * Every handler awaits, and the selection can move while it does. An answer
 * for the card that has gone must not be written into the rail showing the
 * one that arrived.
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
 * Expose a parameter: `entry` (`{slot_label, input_name, value, name}`, the
 * dialog's pick) becomes one of the workflow's own values, set each run, under
 * the name it was given.
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
        { ...address, value: entry.value, name: entry.name },
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

/** What Open in ComfyUI is described by: why it refuses, or what it opens. */
const openDescribedBy = computed(() => {
  if (multiple.value) return "wftab-open-reason";
  if (comfyuiLacksNode.value) return "wftab-open-node-reason";
  return changeCount.value ? "wftab-open-unsaved" : undefined;
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
 * Open ComfyUI on what Run… runs (`runTarget`), in a new tab. `original`
 * opens it with its own nodes rather than PixlStash's (`useComfyuiOpen`).
 *
 * @param {boolean} [original]
 */
function openInComfyui(original = false) {
  const target = runTarget.value;
  if (!target || comfyuiLacksNode.value) return;
  openWorkflowInComfyui(target.id, original);
}

/** The More menu's *Open original in ComfyUI*. */
function openOriginal() {
  menuOpen.value = false;
  openInComfyui(true);
}

watch(
  selectedKey,
  (key) => {
    othersOpen.value = false;
    exposeOpen.value = false;
    versionError.value = "";
    draftError.value = "";
    naming.value = false;
    newName.value = "";
    // The read kept is the last workflow's. Coming straight back to that one
    // must not draw it again as read: a write of it may have answered since.
    changesRead.value = null;
    void loadChanges(key);
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
 * Ask the run pre-flight whether the base model loads, once per card.
 *
 * Keyed on the card alone: every write answers with a new `detail` too, and a
 * pin toggle is no reason to ask ComfyUI again. Asked the moment the detail is
 * in, and `cached`: the server answers from the `object_info` it last read
 * rather than fetching megabytes per card, which is what kept the rows
 * changing most of a second after they were drawn. This only draws; Run…
 * asks afresh. A superseded answer is dropped, and a failed or unanswerable
 * check leaves the card's own answer standing.
 */
watch(
  () => (detail.value ? selectedKey.value : null),
  (key) => checkInstalled(key),
  { immediate: true },
);

/**
 * Pick the shelf's likely match for each missing file (`likely`: the same
 * file under another name), as unsaved changes the owner saves or takes back.
 * Once per selection, when the offers are in and the changes can be edited:
 * a match taken back stays taken back until the card is selected again.
 */
let prefilled = null;
watch(
  () => [replacementsByFile.value, canEdit.value],
  () => {
    const key = selectedKey.value;
    if (!key || prefilled === key || !canEdit.value) return;
    if (!Object.keys(replacementsByFile.value).length) return;
    prefilled = key;
    const picks = modelRows.value
      .filter((row) => row.settable && row.missing && !row.pick)
      .map((row) => [
        row,
        replacementsByFile.value[`${row.kind}:${row.file}`]?.replacements?.find(
          (candidate) => candidate.likely,
        ),
      ])
      .filter(([, likely]) => likely);
    if (!picks.length) return;
    edit((set) =>
      picks.reduce(
        (next, [row, likely]) =>
          withModel(next, row.address, likely.filename, row.file),
        set,
      ),
    );
  },
);

async function checkInstalled(key, { quiet = false } = {}) {
  const check = ++installedCheck;
  // `quiet`: the rows keep what they say until the new answers are in.
  if (!quiet) {
    missingByKind.value = {};
    staleFiles.value = new Set();
    preflightAnswered.value = false;
    replacementsByFile.value = {};
  }
  if (!key) return;
  try {
    const answer = await preflightWorkflowRun(
      { workflow_id: key, values: [] },
      { cached: true },
    );
    if (check !== installedCheck || !stillOn(key)) return;
    preflightAnswered.value = true;
    const reasons = (answer?.groups ?? []).flatMap(
      (group) => group.reasons ?? [],
    );
    const modelsOf = (code) =>
      reasons
        .filter((reason) => reason.code === code)
        .flatMap((reason) => reason.models ?? []);
    // Still a row that needs something done, worded as a restart.
    const stale = modelsOf(PACK_OUTDATED);
    staleFiles.value = new Set(stale.map((model) => String(model.file)));
    const missing = [...modelsOf("missing_models"), ...stale];
    missingByKind.value = Object.fromEntries(
      Object.entries(MODEL_ROWS).map(([kind, spec]) => [
        kind,
        [
          ...new Set(
            missing
              .filter((model) => spec.folders.has(model.folder))
              .map((model) => String(model.file)),
          ),
          // A forgotten name is no file anybody could replace; a base model
          // keeps it, because the row still has to say one is missing.
        ].filter((file) => kind === "checkpoint" || file !== FORGOTTEN_MODEL),
      ]),
    );
  } catch (err) {
    // An unreachable ComfyUI leaves the card's own answer on what is
    // missing. The pickers are still read: the shelf answers for them.
    console.warn(`[workflows] could not pre-flight ${key}`, err);
    if (check !== installedCheck || !stillOn(key)) return;
  }
  // Every row is a picker, so every row's file is asked about, once each.
  const asks = [
    ...new Map(
      modelRows.value
        .filter((row) => row.settable && row.file)
        .map((row) => [`${row.kind}:${row.file}`, [row.kind, row.file]]),
    ).values(),
  ];
  const results = await Promise.allSettled(
    asks.map(([kind, file]) =>
      readModelSwap(key, { replacing: file, slotKind: kind, cached: true }),
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
   used, Set each run and Pinned. */
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

/* A group's count: a raspberry pill, the charts' hue. As text the raspberry
   reads 2.3:1 on the dark panel, so it is the fill and on-secondary the label
   (4.91:1 in both themes). */
.wftab-count {
  margin-left: var(--space-3);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: var(--badge-size);
  height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-secondary));
  color: rgb(var(--v-theme-on-secondary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  line-height: 1;
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

/* Edited and Removed are not saved yet, and say so with the shelf's teal: a
   dot before the word, which still carries the meaning by itself. Not on
   "Yours": that value is saved. */
.wftab-yours--unsaved::before {
  content: "";
  display: inline-block;
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  margin-right: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-surface-tertiary));
}

/* The automatic fix's mark: a free-standing glyph at the text's size, with
   the gap an icon keeps from its label (design §8). Focusable, so its
   tooltip opens from the keyboard too. */
.wftab-fix-mark {
  display: inline-flex;
  vertical-align: text-bottom;
  margin-right: var(--space-2);
  border-radius: var(--radius-sm);
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

/* A missing file's name is often one long word: break it anywhere rather
   than let it run past the column. */
.wftab-model {
  gap: var(--space-2);
  overflow-wrap: anywhere;
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

/* The shelf of unsaved changes: the count and Discard on one line, the two
   verbs on the next. A teal band, so the footer has two coloured regions with
   two meanings: teal keeps, amber runs. The wash is `tertiary` at 26% over
   the rail, the ground `surface-tertiary` is measured on
   (`UNSAVED_WASH`, contrastAudit.js). */
.wftab-draft {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4) var(--space-4);
  border-top: var(--rail-w) solid rgb(var(--v-theme-tertiary));
  background: rgba(var(--v-theme-tertiary), 0.26);
}

/* The landing pulse (visual-language.md §10), once, when the first change is
   made: a glow up the rail toward the row that made it, then rest. */
.wftab-draft--arrived {
  animation: wftab-draft-arrive 2.2s ease-out;
}

@keyframes wftab-draft-arrive {
  35% {
    box-shadow: 0 calc(-1 * var(--space-3)) var(--space-6)
      rgba(var(--v-theme-surface-tertiary), 0.28);
  }
}

@media (prefers-reduced-motion: reduce) {
  .wftab-draft--arrived {
    animation: none;
  }
}

.wftab-draft__line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  /* Discard's own padding, so its word ends on the content edge. */
  margin-right: calc(-1 * var(--space-3));
}

.wftab-draft__count {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
}

.wftab-draft__count > .v-icon {
  color: rgb(var(--v-theme-surface-tertiary));
}

.wftab-draft__name {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.wftab-draft__verbs {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

/* Save takes what the row leaves, so it is plainly the default of the two. */
.wftab-draft__save {
  flex: 1;
}

/* The theme's `border` is under 1.1:1 on the wash: the outline takes the teal
   ink instead, and the two verbs that keep are the two teal things here. */
.wftab-draft .wftab-draft__other {
  border-color: rgb(var(--v-theme-surface-tertiary));
}

.wftab-draft__input,
.wftab-strength {
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: inherit;
  font: inherit;
  font-size: var(--text-sm);
}

.wftab-draft__input {
  width: 100%;
  height: var(--control-h);
  padding: 0 var(--space-3);
}

/* A strength in its row: wide enough for "-0.85", right-aligned so the
   figures of a column of rows line up. */
.wftab-strength {
  flex-shrink: 0;
  width: var(--space-8);
  height: var(--control-h-sm);
  padding: 0 var(--space-2);
  font-variant-numeric: tabular-nums;
  text-align: right;
  appearance: textfield;
}
.wftab-strength::-webkit-inner-spin-button,
.wftab-strength::-webkit-outer-spin-button {
  appearance: none;
}

/* A loader that waits to be removed: its field recedes and its name is
   struck, with Put back where the × was. */
.wftab-lora-gone {
  border-style: dashed;
  background: none;
}
.wftab-lora-gone > s {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The × and Put back sit in the field: pulled to its edge by the field's
   own padding. */
.wftab-lora-value > .app-btn {
  flex-shrink: 0;
  margin-right: calc(-1 * var(--space-2));
}

.wftab-checks {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-2);
}

.wftab-check {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h-sm);
  font-size: var(--text-sm);
  cursor: pointer;
}

.wftab-check > input {
  width: var(--gutter-glyph);
  height: var(--gutter-glyph);
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
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
