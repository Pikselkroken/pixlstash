<template>
  <AppDialog
    open
    size="lg"
    :title="title"
    :subtitle="subtitle"
    :persistent="submitting || dirty"
    @close="onRequestClose"
    @keydown.esc="onEscape"
  >
    <p v-if="loadFailed" class="rund-note rund-note--bad" role="alert">
      {{ loadFailed }}
    </p>
    <p v-else-if="loading" class="rund-note" role="status">
      Reading what this runs…
    </p>

    <div v-else class="rund">
      <!-- ── Left: what the run is made from ──────────────────────────────
           168px wide and the picture 168×252, which is the design's own
           measurement and nothing else in the app uses: a component-local
           value rather than a token nobody else would read. -->
      <div class="rund-src">
        <img
          v-if="coverUrl"
          class="rund-pic"
          :src="coverUrl"
          :alt="`The picture this run is made from: ${sourceName}`"
        />
        <span v-else class="rund-pic rund-pic--empty">
          <v-icon size="28">mdi-sitemap-outline</v-icon>
        </span>
        <div class="rund-meta">
          <span class="rund-name">{{ sourceName }}</span>
          <span class="rund-sub">{{ sourceKindLine }}</span>
        </div>
        <dl class="rund-kv">
          <dt>Results go to</dt>
          <dd v-if="!picksDestination">{{ destinationLine }}</dd>
          <dd v-else>
            <AppSelect
              v-model="destinationSetId"
              label="Results go to"
              hide-label
              compact
              :options="setOptions"
            />
          </dd>
          <template v-if="seedText">
            <dt>Seed of the original</dt>
            <dd class="rund-mono">{{ seedText }}</dd>
          </template>
        </dl>
      </div>

      <!-- ── Right: the form, four columns ──────────────────────────────── -->
      <div ref="formRoot" class="rund-form">
        <div class="rund-f rund-f--4">
          <span class="rund-l">
            Workflow<span class="rund-sp" />
            <!-- Not on the Workflows view itself: there the card is already
                 in the grid behind this popup. -->
            <AppButton
              v-if="activeKey && route?.name !== 'workflows'"
              size="sm"
              variant="ghost"
              icon-left="sitemap-outline"
              tooltip="Close this and show the workflow in the Workflows view"
              :disabled="submitting"
              @click="openInWorkflows"
            >
              Open in Workflows
            </AppButton>
          </span>
          <AppSelect
            v-model="workflowId"
            label="Workflow"
            hide-label
            compact
            :options="workflowOptions"
            :disabled="submitting"
          />
        </div>

        <p v-if="loraFitNote" class="rund-f rund-f--4 rund-note">
          {{ loraFitNote }}
        </p>

        <p v-if="fellBack.length" class="rund-f rund-f--4 rund-note" role="status">
          {{ fellBackLine }}
        </p>

        <!-- ── Pictures in (#1457): one row per picture input of the card,
             straight off the pre-flight, which also decides how each is
             filled. The popup never re-derives that: `fill` is the server's
             answer, so a card whose one open input the selection fills shows
             the selection there without anybody having asked for it. -->
        <div
          v-if="pictureInputs.length"
          class="rund-f rund-f--4 rund-pics"
          role="group"
          :aria-labelledby="picsLabelId"
        >
          <span :id="picsLabelId" class="rund-l">Pictures</span>
          <div
            v-for="input in pictureInputs"
            :key="address(input)"
            class="rund-in"
          >
            <!-- The selection is not a picker: it is the pictures the run
                 repeats over, so it is shown as what it is, and changed by
                 moving it rather than by choosing one picture. -->
            <span
              v-if="input.fill === 'selection'"
              class="rund-in-tile rund-in-tile--sel"
            >
              <img
                v-for="id in pictureIds.slice(0, 4)"
                :key="id"
                class="rund-in-cell"
                :src="pictureThumbnailUrl(id)"
                alt=""
              />
            </span>
            <!-- `aria-disabled`, never `disabled`, on every control in these
                 rows: a natively-disabled button drops out of the tab order
                 while its own write is in flight, and a keyboard user's focus
                 falls to the page. The handlers do the refusing. -->
            <button
              v-else
              type="button"
              class="rund-in-tile"
              :class="{
                'rund-in-tile--empty': !input.picture_id,
                'rund-in-tile--off': !setupReady,
              }"
              :data-input="address(input)"
              :aria-label="
                input.picture_id
                  ? `Change the picture for ${inputTitle(input)}`
                  : `Choose a picture for ${inputTitle(input)}`
              "
              :aria-disabled="setupReady ? undefined : 'true'"
              @click="openPicker(input)"
            >
              <img
                v-if="input.picture_id"
                class="rund-in-img"
                :src="pictureThumbnailUrl(input.picture_id)"
                alt=""
              />
              <v-icon v-else size="24">mdi-image-plus-outline</v-icon>
            </button>
            <span class="rund-in-text">
              <span class="rund-in-title">{{ inputTitle(input) }}</span>
              <span
                class="rund-in-sub"
                :class="{ 'rund-in-sub--bad': input.fill === null }"
              >
                {{ inputLine(input) }}
              </span>
              <AppButton
                v-if="pictureIds.length && input.fill !== 'selection'"
                size="sm"
                class="rund-in-move"
                :aria-disabled="setupReady ? undefined : 'true'"
                @click="useSelectionHere(input)"
              >
                Use my selection here
              </AppButton>
            </span>
            <!-- Keep this picture: a pin is the card's own setup, so it holds
                 for every later run of this workflow in this library. -->
            <!-- One fixed name; `aria-pressed` alone says whether it is on,
                 so a reader never hears "Stop keeping... pressed". -->
            <AppBarButton
              v-if="input.picture_id && input.fill !== 'selection'"
              :icon="input.fill === 'fixed' ? 'pin' : 'pin-outline'"
              :active="input.fill === 'fixed'"
              :aria-pressed="input.fill === 'fixed' ? 'true' : 'false'"
              :aria-disabled="setupReady ? undefined : 'true'"
              :tooltip="`Keep this picture for ${inputTitle(input)} on every run`"
              @click="togglePin(input)"
            />
            <span v-else class="rund-x-gap" />
            <AppBarButton
              v-if="input.fill === 'request'"
              icon="close"
              :tooltip="`Clear the picture for ${inputTitle(input)}`"
              :aria-disabled="setupReady ? undefined : 'true'"
              @click="clearPick(input)"
            />
            <span v-else class="rund-x-gap" />
          </div>
          <p v-if="inputsError" class="rund-note rund-note--bad" role="alert">
            {{ inputsError }}
          </p>
        </div>

        <div class="rund-f rund-f--4">
          <span class="rund-l">
            Prompt
            <RunResetChip
              v-if="!promptUnset && prompt !== basePrompt"
              :value="basePrompt || 'no prompt'"
              label="Prompt"
              @reset="prompt = basePrompt"
            />
          </span>
          <!-- No box the run cannot honour (#1832): a prompt typed here would
               be dropped, so the field says so instead of taking one. -->
          <p v-if="promptUnset" class="rund-note" role="status" data-testid="rund-prompt-unset">
            This workflow has no prompt that can be set here. The run uses the
            workflow as it is saved.
          </p>
          <AppTextarea
            v-else
            ref="promptField"
            v-model="prompt"
            label="Prompt"
            :placeholder="isEdit ? 'Describe the change, e.g. make it night time' : ''"
            :rows="3"
            :disabled="submitting"
            :highlight="personTrigger ? [personTrigger] : []"
            @keydown.stop
          />
          <!-- The person's LoRA answers to a word the prompt does not say.
               A suggestion, never a blocker: it goes once the word is typed,
               and the box then marks the word instead. -->
          <p
            v-if="triggerMissing"
            class="rund-note rund-note--bad rund-trigger"
            role="status"
            data-testid="rund-trigger"
          >
            <v-icon size="14" class="rund-trigger-glyph" aria-hidden="true"
              >mdi-alert-outline</v-icon
            >
            <span>
              The prompt does not have the trigger word of this person's LoRA:
              <strong>{{ personTrigger }}</strong>
            </span>
            <AppButton size="sm" :disabled="submitting" @click="addTrigger"
            >
              Add it
            </AppButton>
          </p>
        </div>

        <!-- Who the run is of. A workflow run by itself carries no person's
             LoRA (the server leaves it out), so the people whose LoRA works
             with its checkpoint are offered here, by face. Opened on a person
             (Create with LoRA…), that person is drawn chosen and fixed. -->
        <div v-if="showPerson" class="rund-f rund-f--4" data-testid="rund-person">
          <span class="rund-l">Person</span>
          <PersonPicker
            v-if="personTiles.length"
            :people="personTiles"
            :model-value="chosenPersonId"
            :fixed="Boolean(fixedPerson)"
            :disabled="submitting"
            aria-label="Person"
            @update:model-value="pickPerson"
          />
          <p v-if="personNote" class="rund-note">{{ personNote }}</p>
        </div>

        <div class="rund-f rund-f--4">
          <span class="rund-l">
            LoRAs<span class="rund-sp" /><span class="rund-l2">Strength</span>
            <span class="rund-x-gap" />
          </span>
          <p v-if="!loraSlots.length" class="rund-note">
            {{ ownLorasLine }}
          </p>
          <template v-for="(row, index) in loras" :key="row.key">
          <!-- A graph LoRA skipped for this run. The popup never changes the
               workflow, so its word is Skip, never delete: the row stays,
               says so, and Use takes it back. -->
          <div
            v-if="row.skipped"
            class="rund-lora rund-lora--skipped"
            :data-lora="row.key"
          >
            <span class="rund-lora-skip-line">
              <span class="rund-lora-name">{{ rowName(row) }}</span>
              <span class="rund-lora-skipped">Skipped for this run</span>
            </span>
            <AppButton
              size="sm"
              data-focus="use"
              :aria-label="`Use ${rowName(row)} in this run`"
              :disabled="submitting"
              @click="useGraphLora(row)"
            >
              Use
            </AppButton>
          </div>
          <div v-else class="rund-lora" :data-lora="row.key">
            <AppSelect
              v-model="row.sha256"
              :label="`LoRA ${index + 1}`"
              hide-label
              compact
              :options="optionsFor(row)"
              :disabled="submitting"
            />
            <AppInput
              v-model.number="row.strength"
              :aria-label="`Strength of LoRA ${index + 1}`"
              type="number"
              min="-10"
              max="10"
              :disabled="submitting"
              @keydown.stop
            />
            <!-- Two different gestures, so two controls. A row the owner ADDED
                 was never in the graph, and its × takes that override away. A
                 slot the GRAPH carries gets Skip: this run is sent with that
                 loader bypassed (`skip_loras`; the server rewires around it),
                 so the owner can run without a LoRA that does not exist here.
                 Skip, not a trash: this popup changes no workflow, and a
                 delete glyph would promise that it does. Editing the chain for
                 good is Edit LoRAs…, on the Workflows screen. -->
            <AppBarButton
              v-if="row.added"
              class="rund-lora-act"
              icon="close"
              :tooltip="`Remove LoRA ${index + 1}`"
              :disabled="submitting"
              @click="removeLora(index)"
            />
            <AppButton
              v-else
              class="rund-lora-act"
              size="sm"
              data-focus="skip"
              :aria-label="`Skip ${rowName(row)} for this run`"
              :disabled="submitting"
              @click="skipGraphLora(row)"
            >
              Skip
            </AppButton>
          </div>
          <p
            v-if="!row.skipped && loraFlag(row)"
            class="rund-note rund-lora-flag"
            data-testid="rund-lora-flag"
          >
            <v-icon size="14" class="rund-lora-flag-glyph" aria-hidden="true"
              >mdi-alert-outline</v-icon
            >
            {{ loraFlag(row) }}
          </p>
          </template>
          <!-- LoRAs this run adds in loaders of their own (`add_loras`):
               nothing the graph loads is replaced, so these work on any
               workflow. Their × takes the addition away. -->
          <div
            v-for="(row, index) in addedLoras"
            :key="row.key"
            class="rund-lora"
            :data-lora="row.key"
          >
            <AppSelect
              v-model="row.sha256"
              :label="row.person ? 'LoRA of the person' : `Added LoRA ${index + 1}`"
              hide-label
              compact
              :options="row.person ? personLoraOptions : adapterOptions"
              :disabled="submitting"
            />
            <AppInput
              v-model.number="row.strength"
              :aria-label="`Strength of added LoRA ${index + 1}`"
              type="number"
              min="-10"
              max="10"
              :disabled="submitting"
              @keydown.stop
            />
            <!-- Not on a fixed person's row: the popup was opened to make
                 pictures of them, and taking their LoRA off is another run. -->
            <AppBarButton
              v-if="!(row.person && fixedPerson)"
              class="rund-lora-act"
              icon="close"
              :tooltip="`Remove added LoRA ${index + 1}`"
              :disabled="submitting"
              @click="removeAddedLora(index)"
            />
          </div>
          <p class="visually-hidden" role="status" aria-live="polite">
            {{ loraLive }}
          </p>
          <AppButton
            size="sm"
            icon-left="plus"
            :disabled="submitting"
            @click="addLora"
          >
            Add LoRA
          </AppButton>
        </div>

        <div v-if="sizeFields.length" class="rund-f rund-f--2">
          <span class="rund-l">
            Size
            <RunResetChip
              v-if="sizeEdited"
              :value="`${baseOf(sizeFields[0])} × ${baseOf(sizeFields[1])}`"
              label="Size"
              @reset="resetSize"
            />
          </span>
          <div class="rund-size">
            <AppInput
              v-for="field in sizeFields"
              :key="address(field)"
              :model-value="currentValue(field)"
              :aria-label="field.label"
              type="number"
              :disabled="submitting"
              @update:model-value="(v) => typeValue(field, v)"
              @blur="settleDraft(field)"
              @keydown.stop
            />
          </div>
        </div>

        <div v-for="field in scalarFields" :key="address(field)" class="rund-f">
          <span class="rund-l">
            {{ field.label }}
            <RunResetChip
              v-if="isEdited(field)"
              :value="baseOf(field)"
              :label="field.label"
              @reset="resetValue(field)"
            />
          </span>
          <AppInput
            :model-value="currentValue(field)"
            :aria-label="field.label"
            type="number"
            :disabled="submitting"
            @update:model-value="(v) => typeValue(field, v)"
            @blur="settleDraft(field)"
            @keydown.stop
          />
        </div>

        <!-- The other parameters this workflow sets each run (its lock is
             open in the Workflow tab): asked for here like the ones above. -->
        <div
          v-for="field in otherRunFields"
          :key="address(field)"
          class="rund-f rund-f--2"
        >
          <span class="rund-l">
            {{ field.label }}
            <RunResetChip
              v-if="isEdited(field)"
              :value="baseOf(field)"
              :label="field.label"
              @reset="resetValue(field)"
            />
          </span>
          <AppInput
            :model-value="String(currentValue(field))"
            :aria-label="field.label"
            :disabled="submitting"
            @update:model-value="(v) => typeValue(field, v)"
            @blur="settleDraft(field)"
            @keydown.stop
          />
        </div>

        <!-- A sampler or scheduler this ComfyUI does not have (`missing_choices`):
             the run uses the one picked here, euler / simple when listed. For
             this run only; the workflow keeps its own value. -->
        <div
          v-for="fix in choiceFixes"
          :key="`${fix.node_id}/${fix.field}`"
          class="rund-f rund-f--2"
          data-testid="rund-choice"
        >
          <span class="rund-l">{{ fix.field }}</span>
          <AppSelect
            v-model="fix.chosen"
            :label="`${fix.field} for this run`"
            hide-label
            compact
            :options="fix.options"
            :disabled="submitting"
          />
          <p class="rund-note">
            {{ fix.value }} is not on this ComfyUI, so this run uses the one
            picked here.
          </p>
        </div>

        <div class="rund-f">
          <span class="rund-l">Count</span>
          <AppInput
            v-model.number="count"
            aria-label="How many to make"
            type="number"
            min="1"
            :max="String(MAX_COUNT)"
            :disabled="submitting"
            @keydown.stop
          />
        </div>

        <div class="rund-f rund-f--3">
          <span class="rund-l">Seed</span>
          <AppSelect
            v-model="seedMode"
            label="Seed"
            hide-label
            compact
            :options="seedOptions"
            :disabled="submitting"
          />
          <!-- Deliberately a TEXT field: `type="number"` hands back a
               `Number`, which is exactly the rounding this avoids. -->
          <AppInput
            v-if="seedMode === 'fixed'"
            v-model="seed"
            aria-label="Seed"
            mono
            :error="seedError"
            :disabled="submitting"
            @keydown.stop
          />
        </div>

        <!-- Per run and never remembered (#1457, decision 4): the default
             follows whether a selected picture is actually fed into the graph,
             which is when the new picture is another take of that one. -->
        <!-- Shown only where its label is true: a picture fed into the graph
             is each output's own source, and one picture is the source of
             all of them. A selection nothing reads would stack every output
             behind the FIRST picture, which is not "the ones they came
             from". -->
        <div v-if="stackOffered" class="rund-f rund-f--4 rund-check">
          <input
            :id="stackId"
            v-model="stack"
            class="rund-box"
            type="checkbox"
            :disabled="submitting"
          />
          <label :for="stackId">Stack new pictures with the ones they came from</label>
        </div>

        <!-- The workflow's optional stages the default recipe runs (#1623);
             an unticked stage is sent as `skip_stages`. -->
        <fieldset v-if="stageRows.length" class="rund-f rund-f--4 rund-stages">
          <legend class="rund-l">Stages</legend>
          <div
            v-for="stage in stageRows"
            :key="stage.name"
            class="rund-f rund-check"
          >
            <input
              :id="`${stageId}-${stage.name}`"
              class="rund-box"
              type="checkbox"
              :checked="stageOn(stage.name)"
              :disabled="submitting"
              @change="setStage(stage.name, $event.target.checked)"
            />
            <label :for="`${stageId}-${stage.name}`"
              >{{ stage.label
              }}<span v-if="stage.detail" class="rund-quiet">
                · {{ stage.detail }}</span
              ></label
            >
            <RunResetChip
              v-if="stageOn(stage.name) !== stageDefault(stage.name)"
              :value="stageDefault(stage.name) ? 'on' : 'off'"
              :label="stage.label.toLowerCase()"
              @reset="setStage(stage.name, stageDefault(stage.name))"
            />
          </div>
        </fieldset>

        <!-- The checkpoint is a MODEL of the default recipe, sent as
             `models: [{address, filename}]` (#1623), not a parameter value. -->
        <div v-if="checkpointFiles" class="rund-f rund-f--4">
          <span class="rund-l">Checkpoints</span>
          <span class="rund-mono" data-testid="rund-checkpoints">{{
            checkpointFiles
          }}</span>
        </div>
        <div v-else-if="checkpointModel" class="rund-f rund-f--4">
          <span class="rund-l">
            Checkpoint
            <RunResetChip
              v-if="checkpointEdited"
              :value="checkpointBase"
              label="Checkpoint"
              @reset="pickCheckpoint(checkpointBase)"
            />
          </span>
          <!-- A checkpoint this ComfyUI does not have is offered the shelf's
               of the same base model, so the recipe's LoRAs still fit, or
               every one the workflow can load when none is known to be. -->
          <AppSelect
            v-if="checkpointFix?.options.length"
            :model-value="checkpointValue"
            label="Checkpoint"
            hide-label
            compact
            :options="checkpointOptions"
            :disabled="submitting"
            @update:model-value="pickCheckpoint"
          />
          <AppInput
            v-else
            :model-value="checkpointValue"
            aria-label="Checkpoint"
            mono
            :disabled="submitting"
            @update:model-value="setCheckpoint"
            @keydown.stop
          />
          <p v-if="checkpointFix" class="rund-note" data-testid="rund-checkpoint-missing">
            {{ checkpointFixNote }}
          </p>
        </div>

        <div class="rund-f rund-f--4 rund-more">
          <!-- The negative prompt only opens when the original had one: an
               empty box under every run would read as a field somebody forgot
               to fill in. -->
          <p
            v-if="baseNegative && negativeUnset"
            class="rund-note"
            role="status"
            data-testid="rund-negative-unset"
          >
            This workflow has no negative prompt to set, so the picture's is
            not used.
          </p>
          <details v-else-if="baseNegative" class="rund-disc">
            <summary>Negative prompt</summary>
            <AppTextarea
              v-model="negative"
              label="Negative prompt"
              :rows="2"
              :disabled="submitting"
              @keydown.stop
            />
          </details>
          <!-- Fixed for this workflow: every run uses its value, so it is not
               a field here. Read-only, each with the way to free it, which
               writes the workflow's own choice; there is no one-off override
               of a fixed value. -->
          <details
            v-if="fixedFields.length"
            class="rund-disc"
            data-testid="rund-fixed-params"
          >
            <summary ref="fixedSummary">
              Fixed for this workflow
              <span class="rund-quiet">· {{ fixedFields.length }}</span>
            </summary>
            <div
              v-for="field in fixedFields"
              :key="address(field)"
              class="rund-fixed-row"
            >
              <!-- A value kept from another workflow or a saved recipe still
                   overrides a fixed one for this run, so it keeps its ↺. -->
              <span class="rund-l">
                {{ field.label }}
                <RunResetChip
                  v-if="isEdited(field)"
                  :value="baseOf(field)"
                  :label="field.label"
                  @reset="resetValue(field)"
                />
              </span>
              <span class="rund-fixed-value">{{ currentValue(field) }}</span>
              <AppButton
                size="sm"
                variant="ghost"
                :loading="freeing === address(field)"
                :disabled="submitting || Boolean(freeing)"
                :aria-label="`Set ${field.label} each run`"
                @click="freeField(field)"
              >
                Set each run
              </AppButton>
            </div>
            <p v-if="freeError" class="rund-note" role="alert">{{ freeError }}</p>
          </details>
        </div>

        <!-- Keyed on the position as well as the code: `reasons` is flat-mapped
             across every group, so two groups refusing for the same reason
             would collide on the code alone. -->
        <!-- `alert` is assertive and interrupts; a notice about a run that IS
             going ahead is a `status`. Only a real refusal earns that. -->
        <div
          v-if="runNotes.length"
          class="rund-f rund-f--4 rund-reasons"
          :role="shownRefusals.length ? 'alert' : 'status'"
        >
        <RunReasonNotice
          v-for="(reason, index) in runNotes"
          :key="`${index}:${reason.code}`"
          :reason="reason"
          :busy="preflighting"
          @settings="emit('open-settings', 'comfyui')"
          @retry="runPreflight()"
          @drop-lora="dropLoras"
          @edit-loras="editLoras"
        />
        </div>
        <!-- Save fixed workflow: the run changes nodes on this ComfyUI (a
             replaced seed or text node, a LoRA loaded through the
             ComfyUI-PixlStash loader), so the repaired graph can be kept as a
             workflow of its own. The original is not changed. -->
        <div v-if="canSaveFixed || fixedNote" class="rund-f rund-f--4 rund-fixed">
          <AppButton
            v-if="canSaveFixed"
            size="sm"
            icon-left="content-save-outline"
            :loading="savingFixed"
            :disabled="submitting || savingFixed"
            tooltip="Save a copy of this workflow with these changes, so it runs on this ComfyUI as it is"
            @click="saveFixed"
          >
            Save fixed workflow
          </AppButton>
          <p v-if="fixedNote" class="rund-note" role="status">{{ fixedNote }}</p>
        </div>
        <p v-if="submitError" class="rund-f rund-f--4 rund-note rund-note--bad" role="alert">
          {{ submitError }}
        </p>
      </div>
    </div>

    <template #footer>
      <!-- Status, never an alert: it says a gesture is already done, and the
           `--bad` spelling beside a real run refusal would read as a second
           thing wrong. -->
      <p v-if="keptAs" :id="savedReasonId" class="rund-note rund-kept">
        Already kept as “{{ keptAs.name || "Untitled" }}”.
      </p>
      <p v-if="runBlocker" :id="blockerId" class="rund-note rund-note--bad">
        {{ runBlocker }}
      </p>
      <!-- `aria-disabled`, not `disabled`, for this dialog's own stated
           reason: a natively-disabled button is out of the tab order, so a
           keyboard reader could never reach the sentence saying why it is
           inert. `onSave` does the refusing. -->
      <AppButton
        :icon-left="keptAs ? 'check' : 'bookmark-plus-outline'"
        :disabled="!activeKey || submitting"
        :aria-disabled="keptAs ? 'true' : undefined"
        :aria-describedby="keptAs ? savedReasonId : undefined"
        @click="onSave"
      >
        {{ keptAs ? "Saved" : "Save as recipe" }}
      </AppButton>
      <span class="rund-sp" />
      <AppButton :disabled="submitting" @click="onRequestClose">Cancel</AppButton>
      <!-- `aria-disabled`, not `disabled`: a natively-disabled button is out of
           the tab order, so a keyboard reader could never reach the reason
           `aria-describedby` points at. `submit()` does the actual refusing.
           Same shape as the Recipe tab's own Run button. -->
      <AppButton
        variant="primary"
        icon-left="play"
        :loading="submitting"
        :class="{ 'run-refused': !canRun }"
        :aria-disabled="canRun ? undefined : 'true'"
        :aria-describedby="runBlocker ? blockerId : undefined"
        @click="submit"
      >
        {{ runLabel }}
      </AppButton>
    </template>
  </AppDialog>

  <!-- Its own dialog rather than a mode in this footer: "what the recipe
       keeps" is a list the owner reads and argues with, and a run form is
       already the densest surface in the app. -->
  <SaveRecipeDialog
    v-if="saveOpen"
    :workflow-id="activeKey"
    :models="checkpointEdited ? runModels : null"
    :suggested-name="card?.name || ''"
    :prompt="prompt"
    :negative="negative"
    :loras="recipeLoras"
    :overrides="recipeOverrides"
    :seed="seedMode === 'fixed' ? String(seed).trim() : ''"
    :settings-aside="seedMode === 'keep' ? KEEP_SEED_ASIDE : ''"
    :source-picture-id="pictureIds[0] ?? null"
    @close="saveOpen = false"
    @handoff="emit('close')"
    @saved="onSaved"
  />

  <!-- Mounted while a slot is being filled and not before: the picker reads
       the library's facets when it opens, which a run that never touches a
       picture should not pay for. -->
  <PicturePicker
    v-if="pickerFor !== null"
    :open="pickerFor !== null"
    :subtitle="pickerFor ? `for ${inputTitle(pickerFor)}` : ''"
    @close="pickerFor = null"
    @pick="onPicked"
  />
</template>

<script setup>
/**
 * The Run popup (v1.12 F5) - one dialog for every source.
 *
 * A picture's recipe, a whole selection, or a workflow card off the Workflows
 * view all open this: the left column says what the run is made from and where
 * its output goes, the right is the card's own parameters, prefilled and
 * editable. Nothing here is written back into a workflow - every edit is an
 * override `POST /workflows/run` applies to the graph at submission time, which
 * is what keeps the card's content-addressed identity intact (B7).
 *
 * **An edited field is marked in its label row**, never beside the control: the
 * four-column grid stays aligned only if a chip cannot change a cell's height.
 *
 * **Save as recipe reads Saved once a saved recipe already keeps this look**
 * (#1480), as the lightbox's Recipe tab has since F6 - without it the second
 * identical row was one press away from here.
 */
import { computed, nextTick, reactive, ref, useId, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";

import { getPictureRecipe } from "../../api/comfyui";
import { fetchWorkflowSets, listAdapters } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import { stackMemberIds } from "../../utils/stackMembers";
import { listSavedRecipes } from "../../api/recipes";
import {
  getWorkflowCard,
  listWorkflowCards,
  preflightWorkflowRun,
  readModelSwap,
  runWorkflowCard,
  saveFixedWorkflow,
  setWorkflowInputs,
  setWorkflowPins,
  workflowCoverUrl,
} from "../../api/workflows";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { errorMessage } from "../../utils/apiError";
import { focusLater } from "../../utils/dom";
import { editLorasRoute, loraBase, loraStem } from "../../utils/loraChain";
import { fitPeople, fitWorkflows } from "../../utils/loraWorkflows";
import { namesWord, triggerWord } from "../../utils/triggerWords";
import { wouldDuplicate } from "../../utils/recipeKey";
import { labelPrompts, runTaskLabel } from "../../utils/runTaskLabel";
import { UNMATCHED_REPLACEMENTS_TEXT } from "../../utils/workflowCard";
import { setEachRun } from "../../utils/workflowPins";
import {
  changesNodes,
  MISSING_CHOICES,
  PICTURE_INPUT_UNFILLED,
  LORAS_BYPASSED,
  reasonsBlock,
  STAGE_LABELS,
  repairNotices,
  unplacedNotice,
} from "../../utils/runReasons";
import PersonPicker from "./PersonPicker.vue";
import SaveRecipeDialog from "./SaveRecipeDialog.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import RunReasonNotice from "./RunReasonNotice.vue";
import RunResetChip from "./RunResetChip.vue";

const props = defineProps({
  /**
   * `{kind, pictureIds, workflowId, pickWorkflow, name, coverUrl}` - see
   * `useRunDialogStore`. Read once: a new source is a new popup, which App.vue
   * mounts fresh by keying it on `runOpened`.
   */
  source: { type: Object, default: null },
  /** `{client_id, set_id, project_id, character_id}` from the grid. */
  context: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["close", "run", "open-settings"]);
const router = useRouter();
const route = useRoute();

/**
 * `MAX_RUNS_PER_REQUEST` (`pixlstash/routes/comfyui.py`), which `_plan`
 * applies to the TOTAL across every group. One `target` is one group, so here
 * the total is the count.
 */
const MAX_COUNT = 200;
/** `MAX_SEED_64` in `pixlstash/routes/workflows.py`: ComfyUI's own ceiling. */
const MAX_SEED = 2n ** 64n - 1n;
/** `MAX_DEFAULTS` in `pixlstash/routes/workflows.py`: the `values` ceiling. */
const MAX_VALUES = 200;
/** The numbers drawn as one-column cells, in this order, when set each run. */
const SCALAR_PINNED = ["steps", "cfg", "cfg_scale", "guidance"];
const SIZE_INPUTS = ["width", "height"];
const CHECKPOINT_INPUT = "ckpt_name";
/**
 * Said in the Save dialog when the form is set to keep the source's seed.
 *
 * A saved recipe's seed is one number it holds, and "the seed of whatever
 * picture this run started from" is not a number - `seed_mode` is the run's
 * own choice and no column keeps it. So that choice is dropped on save, and
 * dropping it in silence is what this line stops.
 */
const KEEP_SEED_ASIDE =
  "This run is set to keep the source picture's seed. A recipe cannot hold that choice, so it is not kept: runs from this recipe draw a new seed.";

const SEPARATOR = "/";

const blockerId = useId();
const picsLabelId = useId();
const stackId = useId();
const stageId = useId();
/** Bumped per open, so a slower earlier read cannot write over a later one. */
let loadToken = 0;
const runDialog = useRunDialogStore();
const entityLists = useEntityListsStore();

const loading = ref(false);
const loadFailed = ref("");
const submitting = ref(false);
const submitError = ref("");
const preflighting = ref(false);
const preflightError = ref("");
/** What the server said this body would submit, at the count it was asked at. */
const plannedRuns = ref(0);

const card = ref(null);
/**
 * The workflow's stored pin list: the parameters set each run. `null` is no
 * choice made, so the shared default set applies (`setEachRun`).
 */
const pins = ref(null);
/** The address "Set each run" is writing, and what went wrong if it failed. */
const freeing = ref("");
const fixedSummary = ref(null);
const formRoot = ref(null);
const freeError = ref("");
const recipe = ref(null);
const cards = ref([]);
const adapters = ref([]);
const reasons = ref([]);
/**
 * Replacements for the samplers and schedulers this ComfyUI does not list
 * (`missing_choices`): `{node_id, field, value, options, chosen}`, sent as
 * `choices`. Kept once offered, so the row stays after the reason clears.
 */
const choiceFixes = ref([]);
/**
 * What this run WILL do differently, which is not a reason it would not run.
 *
 * Kept apart from `reasons` because `reasonsBlock` reads that list: a bypassed
 * LoRA drawn as a refusal would block the very run the server is willing to
 * make. They are concatenated for display and nowhere else.
 */
const bypassed = ref([]);
const activeKey = ref("");
/** address -> the label it had on the card it was edited on. */
const editedLabels = reactive({});
/** address -> the owner's value, over the card's own default. */
const edits = reactive({});
/**
 * address -> what an emptied or half-typed number box shows while the owner
 * is still in it. Never sent: the run uses the default until it parses.
 */
const drafts = reactive({});
const fellBack = ref([]);

const prompt = ref("");
/** `RunGroup.prompt` of the last pre-flight: the graph's own prompt facts. */
const graphPrompt = ref(null);
const negative = ref("");
const count = ref(1);
const seedMode = ref("new");
/**
 * The chosen seed, as TEXT and never as a number.
 *
 * ComfyUI draws seeds up to 2**64-1 and a JavaScript `Number` loses digits
 * above 2**53, so `Number("18446744073709551615")` is a different seed and
 * `JSON.stringify` would send that different seed. The digits are carried
 * through untouched; Pydantic parses the string into a Python int exactly.
 * The same reason `GET /comfyui/pictures/{id}/recipe` serves `seed_text`
 * beside `seed` at all.
 */
const seed = ref("0");
const loras = ref([]);
/**
 * LoRAs this run ADDS in a loader of their own (`add_loras`), rows of
 * `{key, sha256, strength}`. Nothing the graph loads is replaced, so they work
 * on a workflow with no loader, or one whose loaders are all taken.
 */
const addedLoras = ref([]);
/** The added rows as the form opened with them, for `dirty`. */
const addedAtOpen = ref("[]");
let addedKey = 0;
/**
 * "Create with LoRA…": the person or picture set the popup was opened on,
 * `{entityType: "character" | "set", entityId, name}`, or null.
 */
const loraSource = computed(() => props.source?.lora || null);
/** The shelf LoRAs attached to `loraSource`, both attachable file kinds. */
const attachedLoras = ref([]);
/** The owner's hand-made workflow sets, which rank the picker. */
const handMadeSets = ref([]);
const destinationSetId = ref("");

// ── Picture inputs (#1457) ───────────────────────────────────────────────
/**
 * Every picture input of the card, as the last pre-flight answered it
 * (`RunGroup.picture_inputs`): the WHOLE set, which is what makes the
 * whole-set `PUT /workflows/{key}/inputs` safe to call from here. A pin or a
 * moved selection is written through that route from this list and nothing
 * else, so a row the popup has not read is never deleted.
 */
const pictureInputs = ref([]);
/** `address -> {slot_label, input_name, picture_id}` picked for this run only. */
const picks = reactive({});
/** The input the picture picker is open for, or `null`. */
const pickerFor = ref(null);
/**
 * A setup gesture in flight, INCLUDING the pre-flight after its write: the
 * next whole-set PUT is built from `pictureInputs`, and until that pre-flight
 * lands the list still says what the card was before the write. Released
 * early, a second pin sent the first one back as a picker.
 */
const inputsBusy = ref(false);
/**
 * The card `pictureInputs` was read for. A write goes to `activeKey`, and a
 * workflow switch moves that at once while the new card's inputs are still
 * being read - so a pin then would PUT one card's rows under another's key and
 * replace its setup with rows that match nothing.
 */
const inputsKey = ref("");
const inputsError = ref("");
/**
 * The stack checkbox, `null` until the owner touches it.
 *
 * Untouched it follows the server: ticked exactly when a selected picture is
 * fed into an input (decision 4). Deliberately NOT `useGenStackPrefsStore`:
 * its old `stackI2IOutputs` key is still on disks from before #1452, defaulted
 * on, and would silently re-read a choice made about a different feature.
 */
const stackChoice = ref(null);

/**
 * Whether the form holds work a stray click would destroy.
 *
 * `AppDialog` dismisses on a backdrop click and on Escape, and `load()` rebuilds
 * every field on the next open, so one misplaced click discards an edited
 * prompt, its LoRAs and every override with no undo. `persistent` is the
 * mechanism the dialog already has for exactly this.
 *
 * It also closes the Enter path: the keyboard contract suppresses `accept` on a
 * persistent dialog, "so a destructive or in-flight accept only fires from its
 * own button" - and this button queues up to 200 ComfyUI runs. `@accept` is not
 * wired at all here for the same reason; Escape is handled below so dismissing
 * an untouched form still works.
 */
/** The LoRA rows the owner changed, as `RunLora` takes them. */
const changedLoras = computed(() =>
  loras.value
    .filter(
      (row) =>
        !row.skipped &&
        row.sha256 &&
        (row.sha256 !== row.baseSha || row.strength !== row.baseStrength),
    )
    .map((row) => ({
      node_id: row.node_id,
      field: row.field,
      sha256: row.sha256,
      strength_model: Number.isFinite(row.strength) ? row.strength : null,
    })),
);

/**
 * The graph loaders skipped for this run, as `skip_loras` takes them.
 *
 * Never also in `loras`: `changedLoras` passes over a skipped row, so a loader
 * is either overridden or skipped, and the route is never asked for both.
 */
const skippedLoras = computed(() =>
  loras.value
    .filter((row) => row.skipped)
    .map((row) => ({ node_id: row.node_id, field: row.field || "lora_name" })),
);

const dirty = computed(
  () =>
    skippedLoras.value.length > 0 ||
    Object.keys(stageChoice).length > 0 ||
    checkpointEdited.value ||
    prompt.value !== basePrompt.value ||
    negative.value !== baseNegative.value ||
    Object.keys(edits).length > 0 ||
    Object.keys(picks).length > 0 ||
    loras.value.length !== initialLoraCount.value ||
    changedLoras.value.length > 0 ||
    addedSignature.value !== addedAtOpen.value,
);

/** The added rows as `add_loras` takes them; a row with no LoRA picked is left out. */
const addLorasBody = computed(() =>
  addedLoras.value
    .filter((row) => row.sha256)
    .map((row) => ({
      sha256: row.sha256,
      strength_model: Number.isFinite(row.strength) ? row.strength : null,
    })),
);
const addedSignature = computed(() =>
  JSON.stringify(addedLoras.value.map((row) => [row.sha256, row.strength])),
);

/** How many LoRA rows the form opened with, for `dirty`. */
const initialLoraCount = ref(0);

/** Whether the Save as recipe dialog is up over this one. */
const saveOpen = ref(false);

const LAST_SET_KEY = "pixlstash:runDialogSetId";

/**
 * The card being run, and switching to another.
 *
 * A writable computed rather than a `watch` on the ref: the load path assigns
 * the key itself, and a watcher cannot tell that assignment from the owner
 * choosing the first workflow in "Run a workflow on these…", where the previous
 * value is the empty string either way. Here the two are different call sites.
 */
const workflowId = computed({
  get: () => activeKey.value,
  set: (key) => {
    if (!key || key === activeKey.value) return;
    const hadCard = Boolean(card.value);
    activeKey.value = key;
    void switchCard(key, hadCard);
  },
});

async function switchCard(key, keepEdits) {
  // What the last pre-flight and save said is about the card being left.
  changedNodes.value = false;
  fixedNote.value = "";
  // Addressed by node, which is the card being left's.
  choiceFixes.value = [];
  // A pick is addressed by a slot of the card it was made on; another card's
  // slots are other slots, so nothing carries over - and neither do the rows,
  // which are the set a pin would be written from.
  clearPicks();
  pictureInputs.value = [];
  inputsKey.value = "";
  // The same generation the load uses. A card read still in flight when the
  // popup is closed and reopened on another source would otherwise resolve
  // into the new dialog and replace its card, and the pre-flight behind it
  // would replace the new dialog's refusals with the old gesture's.
  const token = loadToken;
  try {
    await loadCard(key, { keepEdits });
    if (token !== loadToken) return;
    await runPreflight(token);
  } catch (err) {
    if (token === loadToken) {
      loadFailed.value = errorMessage(err, "Could not read that workflow.");
    }
  }
}

/**
 * The checkpoint of the workflow's default recipe (#1623), or null: the
 * detail read fills `default_recipe`, and its `address` is what a run's
 * `models` pin names.
 */
const checkpointModels = computed(() =>
  (card.value?.default_recipe?.models || []).filter(
    (model) => model.kind === "checkpoint" && model.address,
  ),
);
const checkpointModel = computed(() => checkpointModels.value[0] || null);
/**
 * The files of a workflow that loads two or more DIFFERENT base models (a Wan
 * 2.2 high + low pair), or null. Such a row is read-only and lists them all:
 * nothing tells the owner which loader is high and which low, so one editable
 * box would invite a file onto a loader it does not belong to. The run sends no
 * `models` for it, and the default recipe loads every one as stored.
 */
const checkpointFiles = computed(() => {
  const files = baseModelFiles(card.value);
  return files.length > 1 ? files.join(" + ") : null;
});

/** A card's distinct checkpoint files; a slot with no file names none. */
function baseModelFiles(detailCard) {
  return [
    ...new Set(
      (detailCard?.default_recipe?.models || [])
        .filter((model) => model.kind === "checkpoint" && model.address)
        .map((model) => model.filename)
        .filter(Boolean),
    ),
  ];
}
/** The owner's checkpoint, or null while the row is untouched. */
const checkpointEdit = ref(null);

/**
 * The card's parameters. Its `ckpt_name` is left out where the default recipe
 * names the checkpoint as a MODEL: that row is sent as `models`, and sending
 * the same widget as a value too would give the run two answers.
 */
const defaults = computed(() => {
  const all = card.value?.defaults || [];
  return checkpointModel.value
    ? all.filter((field) => field.input_name !== CHECKPOINT_INPUT)
    : all;
});
const hasRecipe = computed(() => Boolean(recipe.value));
/**
 * What the LoRA section says when it has no graph rows to show: a card run
 * reads no picture's recipe, so the workflow's own LoRAs are named from its
 * default recipe and run as it stores them. A person's LoRA is never one of
 * them (the Person field says so).
 */
const ownLorasLine = computed(() => {
  if (hasRecipe.value) {
    return "This workflow has no LoRA loader. Add LoRA puts one in for this run.";
  }
  const own = (card.value?.default_recipe?.loras || [])
    .map((row) => loraStem(row.filename || "") || row.filename)
    .filter(Boolean);
  if (own.length) {
    return `This workflow's own LoRAs run as it stores them: ${own.join(", ")}.`;
  }
  return "Add LoRA puts a LoRA in for this run; the workflow keeps its own.";
});
const loraSlots = computed(() => recipe.value?.lora_slots || []);
const pictureIds = computed(() => props.source?.pictureIds || []);
const kind = computed(() => props.source?.kind || "picture");
/**
 * The saved recipe this popup was opened on, when it was opened from one.
 *
 * It is a SOURCE, not a prefill: `POST /workflows/run` takes
 * `saved_recipe_id` and fills the row's prompt, LoRAs, overrides and seed in
 * underneath whatever this form sends, so the form shows the recipe and the
 * owner's edits win over it. The form is prefilled all the same, because
 * `runBody` sends every parameter it displays and a row the form did not show
 * would otherwise be overwritten by the card's own default.
 */
const savedRecipe = computed(() => props.source?.savedRecipe || null);
/**
 * A card with no source picture picks where its output is filed - unless it
 * was opened on a person, whose results go to that person (their reference
 * set, which is what `destination.character_id` does).
 */
const picksDestination = computed(
  () => !pictureIds.value.length && loraSource.value?.entityType !== "character",
);

/**
 * "edit" is "Edit with ComfyUI…" (the grid's menus): the built-in image edit
 * card run over the selection, which fills its picture input. The pictures are
 * what is edited, not recipes to replay, so none of their recipes is read.
 */
const isEdit = computed(() => kind.value === "edit");
const title = computed(() => {
  if (isEdit.value) return "Edit with ComfyUI";
  if (loraSource.value) return "Create with LoRA";
  return kind.value === "card" ? "Run workflow" : "Run recipe";
});
const subtitle = computed(() => card.value?.name || "");
/**
 * What a run's row in the Tasks tab is called (`runTaskLabel`), given the
 * run's own *answer*.
 *
 * A clone is one picture's recipe on its own workflow, nothing on the form
 * changed, run with THAT picture's seed: its seed typed as the fixed one, or
 * "Keep the original's" when the answer says the graph that ran was read from
 * this picture (`source_picture_id`). Kept on another picture's graph, the
 * seed is that picture's, and the run is not this one again.
 */
function taskLabelFor(answer) {
  const sameSeed =
    seedMode.value === "fixed"
      ? Boolean(seedText.value) && String(seed.value).trim() === seedText.value
      : seedMode.value === "keep" &&
        Number(answer?.groups?.[0]?.source_picture_id) ===
          Number(pictureIds.value[0]);
  return runTaskLabel({
    // `runsOwnWorkflow` is only ever true of ONE picture: no recipe is read
    // for several.
    clone: runsOwnWorkflow.value && !dirty.value && sameSeed,
    recipe: savedRecipe.value?.name,
    workflow: card.value?.name,
  });
}
const sourceName = computed(
  () => props.source?.name || card.value?.name || "This run",
);
/**
 * The picture beside the form, as a browser can actually load it.
 *
 * A card's `covers` carry an API-RELATIVE `url` (`_covers`, `workflows.py`) and
 * an `<img src>` bypasses Axios, so nothing prepends `/api/v1` and nothing
 * appends the share token: used verbatim the browser asks the page origin for
 * a path no route serves and the cover is broken. `workflowCoverUrl` is the
 * one spelling of that fix (F1b hit it on the grid first).
 *
 * A run made from a picture, or from a saved recipe, shows THAT picture: the
 * card's cover is whatever the workflow last made, and beside a recipe's own
 * prompt it reads as a different run altogether.
 */
const coverUrl = computed(() => {
  if (props.source?.coverUrl) return props.source.coverUrl;
  const pictureId = pictureIds.value[0] ?? savedRecipe.value?.source_picture_id;
  if (pictureId != null) return pictureThumbnailUrl(pictureId);
  const cover = card.value?.covers?.[0];
  return cover ? workflowCoverUrl(cover) : "";
});
const seedText = computed(() => recipe.value?.seed_text || "");

const sourceKindLine = computed(() => {
  const many = pictureIds.value.length;
  if (loraSource.value) {
    return loraSource.value.entityType === "character"
      ? "Person, with their LoRA added"
      : "Picture set, with its LoRA added";
  }
  if (savedRecipe.value) return "Saved recipe";
  if (kind.value === "card") return "Workflow, with no picture behind it";
  if (isEdit.value) {
    return many > 1 ? `${many} pictures to edit, one run each` : "The picture to edit";
  }
  if (many > 1) return `${many} pictures, all on this workflow`;
  return "This picture's recipe";
});

function address(field) {
  return `${field.slot_label}${SEPARATOR}${field.input_name}`;
}

/**
 * The value a field started at: the card's own default, and the picture's own
 * setting over it where the picture is what opened this. That is what the ↺
 * chip carries and what it puts back.
 */
function baseOf(field) {
  const ownCard = runsOwnWorkflow.value;
  // And only when the name picks out ONE parameter. `recipe.settings` is
  // `{field: value}` with no slot, so a graph with two samplers both carrying
  // `steps` would otherwise show the same picture value in both rows and send
  // it to both.
  const fromPicture =
    ownCard && !ambiguousInputs.value.has(field.input_name)
      ? recipe.value?.settings?.[field.input_name]
      : undefined;
  return fromPicture !== undefined && fromPicture !== null
    ? fromPicture
    : field.value;
}

/**
 * Whether the workflow being run is the one that MADE the picture. Another
 * workflow is a different graph, so the picture's own settings say nothing
 * about it, and showing them would make its defaults look like edits.
 */
const runsOwnWorkflow = computed(() =>
  recipe.value?.workflow_id
    ? recipe.value.workflow_id === activeKey.value
    : false,
);

/**
 * The models the owner replaced in this workflow because the original is gone
 * (`model_fixes` on the detail read, `PUT …/model-fix`): a run loads `now`
 * wherever the graph names `was`, so the row must show `now`.
 */
const modelFixes = ref([]);

/** The owner's saved replacement for checkpoint *file*, or null. */
function savedReplacement(file) {
  const key = loraBase(file);
  if (!key) return null;
  return (
    modelFixes.value.find(
      (fix) => fix.slot_kind === "checkpoint" && loraBase(fix.was) === key,
    ) || null
  );
}

/** *file*, or what a run loads in its place. */
function inPlaceOf(file) {
  return savedReplacement(file)?.now || file;
}

/** The checkpoint the recipe names: the picture's own, else the default's. */
const checkpointNamed = computed(() => {
  const own = runsOwnWorkflow.value
    ? recipe.value?.settings?.[CHECKPOINT_INPUT]
    : null;
  return String(own || checkpointModel.value?.filename || "");
});
/** The owner's saved replacement for that checkpoint, or null. */
const checkpointReplaced = computed(() => savedReplacement(checkpointNamed.value));
/**
 * The checkpoint the row starts at: what the run will load, which is the
 * saved replacement where the named one is gone.
 */
const checkpointBase = computed(() => inPlaceOf(checkpointNamed.value));
const checkpointValue = computed(() => checkpointEdit.value ?? checkpointBase.value);
const checkpointEdited = computed(() => checkpointEdit.value !== null);

function setCheckpoint(raw) {
  const text = String(raw ?? "").trim();
  checkpointEdit.value = text === checkpointBase.value ? null : text;
}

/**
 * The checkpoint as a run's `models` takes it, or [] when there is no row or
 * nothing in it. The row is always sent, edited or not, for the reason
 * `runBody` sends every value: the form is what runs.
 */
const runModels = computed(() =>
  checkpointModel.value && checkpointValue.value && !checkpointFiles.value
    ? // Every loader of the one file: two loaders of it read as one row.
      checkpointModels.value.map((model) => ({
        address: model.address,
        filename: checkpointValue.value,
      }))
    : [],
);

/** ComfyUI's folders for a base model, as `missing_models` names them. */
const BASE_MODEL_FOLDERS = ["checkpoints", "diffusion_models"];

/**
 * The checkpoint the pre-flight says this ComfyUI does not have, or the saved
 * replacement for one that is gone, with what may replace it:
 * `{file, missing, was, options, reason, narrowed}`, or null. `options` are the
 * shelf checkpoints this workflow's loader can load, held to the missing one's
 * base model where anything says which and any has it (`narrowed`), so the LoRAs still fit
 * (`model-swap?replacing=`, the Workflow tab's "Replace with…"); `reason` says
 * why there are none. Kept once offered, so the picker stays after a pick
 * clears the reason.
 */
const checkpointFix = ref(null);
/** Bumped per replacement read, so only the latest one's answer lands. */
let checkpointAsk = 0;

/** The picker's rows: the missing file first, so the row shows what it was. */
const checkpointOptions = computed(() => {
  const fix = checkpointFix.value;
  if (!fix) return [];
  return [
    {
      value: fix.file,
      label: fix.missing
        ? `${fix.file} (not on this ComfyUI)`
        : `${fix.file} (in place of ${fix.was})`,
    },
    ...fix.options.map((model) => ({
      value: model.filename,
      label: model.display_name || model.filename,
    })),
  ];
});

const checkpointFixNote = computed(() => {
  const fix = checkpointFix.value;
  if (!fix) return "";
  const gone = fix.missing
    ? `${fix.file} is not on this ComfyUI.`
    : `${fix.was} is not on this ComfyUI, so this workflow loads ${fix.file} in its place (set in the Workflow tab).`;
  // Nothing to suggest until the replacements are read.
  if (fix.loading) return gone;
  if (fix.options.length) {
    return fix.narrowed
      ? `${gone} Pick another of the same base model for this run.`
      : `${gone} ${UNMATCHED_REPLACEMENTS_TEXT}`;
  }
  // No `needs_pixlstash_nodes` case: the PixlStash swap loaders are VAE and
  // text-encoder ones only, so a checkpoint ask never gets that reason.
  switch (fix.reason) {
    case "none_loadable":
      return fix.narrowed
        ? `${gone} None of the same base model on your model shelf can be loaded by this workflow.`
        : `${gone} Nothing on your model shelf can be loaded by this workflow.`;
    default:
      return `${gone} Type the name of one it has.`;
  }
});

function pickCheckpoint(filename) {
  setCheckpoint(filename);
  void runPreflight();
}

/**
 * Offer replacements when *found* (a pre-flight's reasons) names the
 * checkpoint the row started at as missing, or when that checkpoint is the
 * owner's saved replacement for one that is gone: the run loads it without a
 * word otherwise, and it may not be the one wanted. Asked once per file: the
 * server reads the whole shelf to answer.
 *
 * The server answers for a file the workflow's graph loads, so a picture's own
 * checkpoint that is not the graph's is asked about as the graph's file, and
 * that file, which fits the graph by construction, leads the offer.
 */
async function offerCheckpoints(found, key, token) {
  const file = checkpointBase.value;
  if (!checkpointModel.value || checkpointFiles.value || !file) return;
  if (checkpointFix.value?.file === file) return;
  const missing = found.some(
    (reason) =>
      reason?.code === "missing_models" &&
      (reason.models || []).some(
        (model) => BASE_MODEL_FOLDERS.includes(model?.folder) && model.file === file,
      ),
  );
  const was = checkpointReplaced.value?.was || "";
  if (!missing && !was) return;
  const fix = {
    file,
    missing,
    was,
    options: [],
    reason: "",
    narrowed: false,
    loading: true,
  };
  checkpointFix.value = fix;
  const ask = ++checkpointAsk;
  // As the graph names it once the owner's fixes are applied, which is what
  // the server reads.
  const graphFile = inPlaceOf(checkpointModel.value.filename || file);
  const current = () =>
    token === loadToken && key === activeKey.value && ask === checkpointAsk;
  let answer;
  try {
    answer = await readModelSwap(key, {
      replacing: graphFile,
      slotKind: "checkpoint",
    });
  } catch (err) {
    console.warn(`[run] could not read replacements for ${graphFile} on ${key}`, err);
    // Asked again on the next pre-flight rather than never.
    if (current()) checkpointFix.value = null;
    return;
  }
  if (!current()) return;
  // Typed over while the read was out: the owner's name stays in its box.
  if (checkpointEdit.value !== null) {
    checkpointFix.value = null;
    return;
  }
  const own = graphFile === file ? [] : [{ filename: graphFile }];
  checkpointFix.value = {
    ...fix,
    options: [...own, ...(answer?.replacements || [])],
    reason: answer?.replacements_reason || "",
    narrowed: Boolean(answer?.replacements_narrowed),
    loading: false,
  };
}

/**
 * The optional stages the base graph carries, as the rows draw them. Only the
 * ones the default recipe runs: the server cannot switch a recipe-off stage
 * back on, so a tickable row for one would promise a run it will not make.
 */
const stageRows = computed(() =>
  (card.value?.specials || [])
    .filter(
      (name) =>
        name in STAGE_LABELS &&
        card.value?.default_recipe?.stages?.[name] !== false,
    )
    .map((name) => ({
      name,
      label: STAGE_LABELS[name],
      // Which kind the stage is, where it can be several (an upscale).
      detail: card.value?.default_recipe?.stage_details?.[name] || "",
    })),
);
/** stage -> the owner's on/off, over the default recipe's. */
const stageChoice = reactive({});

/** The default recipe's on/off for a stage; a stage it does not name runs. */
function stageDefault(name) {
  return card.value?.default_recipe?.stages?.[name] ?? true;
}

function stageOn(name) {
  if (name in stageChoice) return stageChoice[name];
  return stageDefault(name);
}

async function setStage(name, on) {
  const byDefault = stageDefault(name);
  if (on === byDefault) delete stageChoice[name];
  else stageChoice[name] = on;
  // The answer changes: a stage that cannot be taken out is refused
  // (`stage_not_skippable`).
  await runPreflight();
}

function clearStages() {
  for (const name of Object.keys(stageChoice)) delete stageChoice[name];
}

/** The stages this run goes without, as `skip_stages` takes them. */
const skippedStages = computed(() =>
  stageRows.value.filter((row) => !stageOn(row.name)).map((row) => row.name),
);

/** What this run sends for a field: the owner's value, else the default. */
function runValue(field) {
  const key = address(field);
  return key in edits ? edits[key] : baseOf(field);
}

/** What a field's box shows: a half-typed draft over the value it runs at. */
function currentValue(field) {
  const key = address(field);
  return key in drafts ? drafts[key] : runValue(field);
}

/**
 * What a box hands back as the owner types. A box with no number in it yet
 * keeps showing what was typed - writing the default back into it would undo
 * the very keystroke that cleared it - and runs at the default meanwhile.
 */
function typeValue(field, raw) {
  const value = coerce(field, raw);
  const key = address(field);
  if (value === undefined && typeof baseOf(field) === "number") {
    drafts[key] = raw;
  } else {
    delete drafts[key];
  }
  setValue(field, value);
}

/** Leaving a box with no number in it shows what the run will use. */
function settleDraft(field) {
  delete drafts[address(field)];
}

function isEdited(field) {
  return address(field) in edits;
}

/**
 * One typed value, in the type the field started as.
 *
 * `undefined` is "this is not a value": an emptied number box reads as `""`,
 * and `Number("")` is 0, so a field the owner merely cleared would be recorded
 * as a deliberate zero and submitted as one. Anything unparseable is the same
 * answer, since `JSON.stringify(NaN)` is `null` and `RunValue` refuses it.
 */
function coerce(field, raw) {
  if (typeof baseOf(field) !== "number") return raw;
  const text = String(raw ?? "").trim();
  if (!text) return undefined;
  const value = Number(text);
  return Number.isFinite(value) ? value : undefined;
}

/** The pending re-ask after the owner types over a replaced field. */
let choiceReask = null;
const CHOICE_REASK_MS = 300;

function setValue(field, value) {
  const key = address(field);
  // Not a value: the field runs at what it started at rather than recording
  // a zero nobody typed (its box may still show the draft; see typeValue).
  if (value === undefined || value === baseOf(field)) {
    delete edits[key];
    delete editedLabels[key];
  } else {
    edits[key] = value;
    editedLabels[key] = field.label;
  }
  // The owner's own pick for a sampler or scheduler: a replacement offered
  // for it would otherwise win on the server and override it unsaid. Asked
  // again AFTER the edit is recorded, so the re-ask carries it and a pick
  // this ComfyUI still lacks is offered afresh. Trailing, because the field
  // emits per keystroke: a partial name is missing too, and re-asking for
  // each one would re-offer and race.
  if (
    choiceReask ||
    choiceFixes.value.some((fix) => fix.field === field.input_name)
  ) {
    choiceFixes.value = choiceFixes.value.filter(
      (fix) => fix.field !== field.input_name,
    );
    clearTimeout(choiceReask);
    choiceReask = setTimeout(() => {
      choiceReask = null;
      void runPreflight();
    }, CHOICE_REASK_MS);
  }
}

function resetValue(field) {
  const key = address(field);
  delete drafts[key];
  delete edits[key];
  delete editedLabels[key];
}

/** Input names more than one of this card's parameters carries. */
const ambiguousInputs = computed(() => {
  const seen = new Set();
  const twice = new Set();
  for (const field of defaults.value) {
    if (seen.has(field.input_name)) twice.add(field.input_name);
    seen.add(field.input_name);
  }
  return twice;
});

const byInput = computed(() => {
  const map = {};
  for (const field of defaults.value) map[field.input_name] = field;
  return map;
});

/** Whether the Run form asks for `field`, or every run uses its value. */
function asksFor(field) {
  return setEachRun(field, pins.value);
}

const scalarFields = computed(() =>
  SCALAR_PINNED.map((name) => byInput.value[name]).filter(
    (field) => field && asksFor(field),
  ),
);
/**
 * Width and height, drawn as one "Size" cell — and only when BOTH are there
 * and both are set each run.
 *
 * Otherwise each is drawn on its own, as an ordinary row or a fixed one. Half
 * a Size cell would be a control that lies about what it sets.
 */
const sizeFields = computed(() => {
  const both = SIZE_INPUTS.map((name) => byInput.value[name]);
  return both.every((field) => field && asksFor(field)) ? both : [];
});
const pinnedAddresses = computed(
  () =>
    new Set(
      [...scalarFields.value, ...sizeFields.value]
        .filter(Boolean)
        .map(address),
    ),
);
/** Set each run, but not one of the cells above: a plain row each. */
const otherRunFields = computed(() =>
  defaults.value.filter(
    (field) => asksFor(field) && !pinnedAddresses.value.has(address(field)),
  ),
);
/** Fixed: not asked for, listed read-only at the foot. */
const fixedFields = computed(() =>
  defaults.value.filter((field) => !asksFor(field)),
);

/**
 * "Set each run" on a fixed parameter: frees it on the workflow, so this form
 * and every later one asks for it. The whole list is written, the way the
 * Workflow tab writes it, and handed to that tab through the store.
 */
async function freeField(field) {
  const key = activeKey.value;
  if (!key || freeing.value) return;
  const all = card.value?.defaults || [];
  const current = Array.isArray(pins.value)
    ? pins.value
    : all.filter((row) => setEachRun(row, null));
  const next = [...current, field].map((row) => ({
    slot_label: row.slot_label,
    input_name: row.input_name,
  }));
  freeing.value = address(field);
  freeError.value = "";
  const epoch = runDialog.sessionEpoch();
  try {
    const body = await setWorkflowPins(key, next);
    // Answered for a session that has since been reset: it describes a
    // library this one may not see, so none of it is kept.
    if (epoch !== runDialog.sessionEpoch()) return;
    const written = body?.pins ?? next;
    // Told even when the picker has moved on: the write landed on `key`, and
    // the Workflow tab writes whole lists from its own copy.
    runDialog.pinsWritten = { workflowId: key, pins: written };
    if (key !== activeKey.value) return;
    pins.value = written;
    // Its row is gone from under the pointer: keep focus on the list, or on
    // the field it became when the list went with it.
    await nextTick();
    // Searched in this form only: "Steps" may label a field anywhere else.
    const target =
      fixedSummary.value ??
      [...(formRoot.value?.querySelectorAll("input[aria-label]") ?? [])].find(
        (input) => input.getAttribute("aria-label") === field.label,
      );
    target?.focus();
  } catch (err) {
    freeError.value = errorMessage(err, `Could not set ${field.label} each run.`);
  } finally {
    freeing.value = "";
  }
}

const sizeEdited = computed(() => sizeFields.value.some(isEdited));
function resetSize() {
  sizeFields.value.forEach(resetValue);
}

/**
 * The prompt the box opens with, and what its reset chip goes back to.
 *
 * A caller's own `source.prompt` wins: the Recipes tab passes the prompt its
 * row shows, and the picture's re-read can come back without one.
 */
const basePrompt = computed(
  () =>
    props.source?.prompt ||
    recipe.value?.positive_prompt ||
    savedRecipe.value?.prompt ||
    graphPrompt.value?.positive_text ||
    "",
);
/**
 * A run with nothing else to prefill from shows the workflow's own prompt once
 * the pre-flight has read it, so what will run is visible (#1832), and follows
 * it to the next workflow picked. Only a box still showing the last base:
 * text the owner typed is theirs.
 */
watch(basePrompt, (now, was) => {
  if (prompt.value === was) prompt.value = now;
});
/** The graph has nowhere to put a prompt, so the form offers no box for one. */
const promptUnset = computed(() => graphPrompt.value?.positive_settable === false);
const negativeUnset = computed(() => graphPrompt.value?.negative_settable === false);

/**
 * What to send as `prompt`, or `null` to leave the graph's own alone.
 *
 * An empty box is only an instruction to blank the prompt when there WAS one
 * on screen to blank. Without a recipe behind it the box starts empty because
 * nothing filled it, which is not the same thing.
 */
const promptOverride = computed(() => {
  if (promptUnset.value) return null;
  const typed = prompt.value;
  if (typed) return typed;
  return basePrompt.value ? "" : null;
});
const baseNegative = computed(
  () => recipe.value?.negative_prompt || savedRecipe.value?.negative || "",
);

/**
 * The LoRAs as a recipe keeps them: by name and strength, not by slot.
 *
 * A row the shelf could name goes by its digest, which finds it again after a
 * rename. A graph row it could not name goes by its file with an empty digest:
 * Save as recipe flags that row and lets the owner take it off (#1478), which
 * it can only do for a row it is shown.
 */
const recipeLoras = computed(() =>
  // The LoRAs this run adds are part of the look too: the person picked for a
  // workflow run has no graph row, and a recipe saved without them would run
  // as somebody else, or as no one.
  [...loras.value, ...addedLoras.value]
    // A graph LoRA the shelf cannot name is KEPT, as its file with no digest:
    // Save as recipe lists and flags it (#1478), and dropping it here was the
    // silent half of that - the dialog never saw it to say so.
    // A row skipped for THIS run is still kept: a saved recipe cannot hold a
    // skip, so every run of it loads that loader from the graph, and a list
    // leaving it out would say less than those runs do.
    .filter((row) => row.sha256 || (!row.added && row.graphValue))
    .map((row) => {
      if (!row.sha256) {
        return {
          filename: row.graphValue,
          sha256: "",
          strength: strengthOr1(row.strength),
        };
      }
      const shelf = adapters.value.find((item) => item.sha256 === row.sha256);
      return {
        filename:
          shelf?.filename || shelf?.display_name || row.graphValue || row.sha256,
        sha256: row.sha256,
        strength: strengthOr1(row.strength),
      };
    }),
);

/**
 * The parameters the owner changed, each with the value it was changed from.
 *
 * The Save as recipe dialog prints "Steps 12 instead of the workflow's 8", so
 * it needs both numbers; `edits` alone carries only the new one.
 */
const recipeOverrides = computed(() =>
  defaults.value.filter(isEdited).map((field) => ({
    address: address(field),
    label: field.label,
    value: edits[address(field)],
    base: baseOf(field),
  })),
);

// ── Is this look already kept? (#1480) ───────────────────────────
//
// The lightbox's Recipe tab has answered this since F6 and this popup did not,
// so the duplicate the tab refuses was one press away from here. The same
// `keepsTheSameLook` as the tab, off the same read, because two copies of that
// comparison is how it went wrong the first time (`utils/recipeKey.js`).

/** The saved recipes of `activeKey`, and the id they were read for. */
const savedRecipes = ref([]);
const savedForKey = ref("");
const savedReasonId = useId();

/**
 * Exactly what pressing Save would write, which is what the match asks about.
 *
 * `recipeLoras`, not the graph's slots, because the save writes the digested
 * rows and no others - and **the negative prompt and the overrides too**,
 * because this is a form and the save writes those as well. Keying on the
 * look alone (prompt and LoRA names, which is what credit is grouped by)
 * would make **Saved** refuse a recipe that differs from the kept one in
 * every parameter it carries: not a duplicate, a variation, and unsaveable.
 * `wouldDuplicate` is that question, beside the look's own key in
 * `utils/recipeKey.js` so the comparisons stay in one file.
 */
const thisSave = computed(() => ({
  prompt: prompt.value,
  negative: negative.value,
  loras: recipeLoras.value,
  overrides: Object.fromEntries(
    recipeOverrides.value.map((row) => [row.address, row.value]),
  ),
}));

/**
 * The saved recipe a save from this form would duplicate, or null.
 *
 * It comes and goes as the owner types, which is the point: edit the prompt,
 * a LoRA strength or a parameter and this is a new recipe again, so Save
 * comes back.
 */
const keptAs = computed(() => {
  if (!activeKey.value || activeKey.value !== savedForKey.value) return null;
  return (
    savedRecipes.value.find((row) => wouldDuplicate(row, thisSave.value)) ||
    null
  );
});

watch(
  () => activeKey.value,
  async (key) => {
    savedRecipes.value = [];
    savedForKey.value = "";
    if (!key) return;
    try {
      const rows = await listSavedRecipes(key);
      // The picker may have moved on while the read was out.
      if (key !== activeKey.value) return;
      savedRecipes.value = rows;
      savedForKey.value = key;
    } catch (err) {
      // A footer that cannot say "Saved" is not a failure of the run form:
      // the popup's whole job is still on screen, so this is logged and
      // dropped, and the save goes on being offered.
      console.warn("Could not read this workflow's saved recipes:", err);
    }
  },
  { immediate: true },
);

/** The gesture, refused here rather than by a `disabled` nobody can reach. */
function onSave() {
  if (keptAs.value || !activeKey.value || submitting.value) return;
  saveOpen.value = true;
}

/** Straight into the list, so the footer answers without another read. */
function onSaved(row) {
  if (!row) return;
  savedRecipes.value = savedRecipes.value.some((known) => known.id === row.id)
    ? savedRecipes.value.map((known) => (known.id === row.id ? row : known))
    : [...savedRecipes.value, row];
}

const seedOptions = [
  { value: "new", label: "New for each picture" },
  { value: "keep", label: "Keep the original's" },
  { value: "fixed", label: "A seed I choose" },
];

/**
 * The workflows the picker offers: the one being run, and - for "Run a
 * workflow on these…" - the whole library. Workflows only (#1623): there are
 * no stack members to switch between any more.
 */
/**
 * Create with LoRA's narrowing, for the LoRA the first added row carries (or
 * the first one attached, before the owner changes it): see
 * `utils/loraWorkflows.js`.
 */
const loraFits = computed(() => {
  if (!loraSource.value) return null;
  const sha = addedLoras.value[0]?.sha256;
  // The owner took the LoRA off the run: nothing left to narrow for, so the
  // picker offers every workflow rather than one base model the run no longer
  // carries. (With nothing attached at all the fit still runs, to say so.)
  if (!sha && attachedLoras.value.length) return null;
  // A LoRA neither list names narrows nothing: every workflow is then an
  // "unknown" fit, rather than one judged against a different attached LoRA.
  const lora =
    attachedLoras.value.find((row) => row.sha256 === sha) ||
    adapters.value.find((row) => row.sha256 === sha) ||
    null;
  return { lora, ...fitWorkflows(cards.value, lora, handMadeSets.value) };
});

/** What the narrowing left out, said rather than hidden without a word. */
const loraFitNote = computed(() => {
  const fits = loraFits.value;
  if (!fits) return "";
  if (!attachedLoras.value.length) {
    return `No LoRA is attached to ${loraSource.value.name}. Assign one from the Models shelf, or add one below.`;
  }
  if (!fits.lora) {
    return "This LoRA is not on the Models shelf, so every workflow is listed.";
  }
  if (!fits.lora.base_model_family) {
    return "This LoRA has no base model recorded, so every workflow is listed.";
  }
  const parts = [];
  if (fits.clash.length) {
    parts.push(`${fits.clash.length} for another base model`);
  }
  if (fits.needsPicture.length) {
    parts.push(`${fits.needsPicture.length} that start from a picture`);
  }
  if (!fits.match.length && !fits.unknown.length) {
    return `None of your workflows is for ${fits.lora.base_model || "this base model"}.`;
  }
  return parts.length ? `Not listed: ${parts.join(", ")}.` : "";
});

const workflowOptions = computed(() => {
  const fits = loraFits.value;
  if (fits) {
    // Matches first, then the workflows whose base model nobody has read,
    // said so: they may fit, and hiding them would hide every workflow on a
    // model the shelf has not identified.
    const known = Boolean(fits.lora?.base_model_family);
    return [
      ...fits.match.map((entry) => ({ value: entry.card.id, label: entry.card.name })),
      ...fits.unknown.map((entry) => ({
        value: entry.card.id,
        label: known ? `${entry.card.name} (base model not known)` : entry.card.name,
      })),
    ];
  }
  const rows = card.value ? [{ value: card.value.id, label: card.value.name }] : [];
  for (const row of props.source?.pickWorkflow ? cards.value : []) {
    if (!rows.some((option) => option.value === row.id)) {
      rows.push({ value: row.id, label: row.name });
    }
  }
  return rows;
});

const adapterOptions = computed(() =>
  [
    ...adapters.value,
    // An attached LoRA the shelf classes `unknown` is not in `adapters`,
    // which reads `file_kind=adapter` only.
    ...attachedLoras.value.filter(
      (row) => !adapters.value.some((adapter) => adapter.sha256 === row.sha256),
    ),
  ]
    .filter((adapter) => adapter?.sha256)
    .map(adapterOption)
    .sort((a, b) => a.label.localeCompare(b.label)),
);

function adapterOption(adapter) {
  return {
    value: adapter.sha256,
    label: adapter.display_name || adapter.filename || adapter.sha256.slice(0, 12),
  };
}

/**
 * A workflow run by itself is of no one: the server leaves a person's LoRA out
 * of it (`_leave_out_character_loras`, `routes/workflows.py`), and a recipe
 * keeps it. So this run, and only this one, is offered the people to add.
 */
const offersPerson = computed(
  () =>
    kind.value === "card" &&
    !pictureIds.value.length &&
    !savedRecipe.value &&
    !loraSource.value,
);
/** Create with LoRA… on a person: that person, chosen and not changeable. */
const fixedPerson = computed(() =>
  loraSource.value?.entityType === "character"
    ? { id: Number(loraSource.value.entityId), name: loraSource.value.name }
    : null,
);
/** Shelf rows the shelf classes `unknown`: attachable to a person all the same. */
const unknownLoras = ref([]);
/** The people whose LoRA is for this workflow's base model (`fitPeople`). */
const personFits = computed(() =>
  offersPerson.value && card.value
    ? fitPeople(
        card.value,
        [...adapters.value, ...unknownLoras.value],
        entityLists.characters,
      )
    : { people: [], clash: 0, unknown: 0, family: null },
);
/** The person picked for this run, or null for no one. */
const personId = ref(null);
const chosenPersonId = computed(() => fixedPerson.value?.id ?? personId.value);
const personTiles = computed(() =>
  fixedPerson.value ? [fixedPerson.value] : personFits.value.people,
);
/** Absent, not empty, in a library where nobody has a LoRA. */
const showPerson = computed(
  () =>
    Boolean(fixedPerson.value) ||
    personFits.value.people.length > 0 ||
    personFits.value.clash + personFits.value.unknown > 0,
);
const personNote = computed(() => {
  if (fixedPerson.value) return "";
  const { people, clash, unknown, family } = personFits.value;
  if (!family) {
    return "This workflow's base model is not known, so no person's LoRA can be matched to it.";
  }
  const parts = [];
  if (clash) parts.push(`${clash} whose LoRA is for another base model`);
  if (unknown) parts.push(`${unknown} whose LoRA has no base model recorded`);
  const left = parts.length ? ` Not listed: ${parts.join(", ")}.` : "";
  return people.length
    ? `A workflow runs without a person's LoRA. Pick someone to add theirs.${left}`
    : `No person has a LoRA for this workflow's base model, so it runs with no one's.${left}`;
});
/** The LoRAs the person's row offers: theirs that fit, never the whole shelf. */
const personLoraOptions = computed(() =>
  (fixedPerson.value
    ? attachedLoras.value
    : personFits.value.people.find((person) => person.id === personId.value)
        ?.loras || []
  ).map(adapterOption),
);

/**
 * The trigger word of the LoRA on the person's row, or "" when there is no
 * such row or its LoRA needs none. Read off the row's own pick, so changing
 * which of the person's LoRAs runs changes the word.
 */
const personTrigger = computed(() => {
  const sha = addedLoras.value.find((row) => row.person)?.sha256;
  if (!sha) return "";
  return triggerWord(
    [...attachedLoras.value, ...adapters.value, ...unknownLoras.value].find(
      (row) => row.sha256 === sha,
    ),
  );
});
/** The word is asked for and the prompt, which can be set here, lacks it. */
const triggerMissing = computed(
  () =>
    Boolean(personTrigger.value) &&
    !promptUnset.value &&
    !namesWord(prompt.value, personTrigger.value),
);
const promptField = ref(null);

/** Put the word first, where a trigger word goes, and hand the box back. */
function addTrigger() {
  const rest = prompt.value.trim();
  prompt.value = rest ? `${personTrigger.value}, ${rest}` : personTrigger.value;
  // The button goes with the suggestion; focus must not fall to the page.
  promptField.value?.focus?.();
}

/**
 * Pick the person this run is of: their first matching LoRA becomes an added
 * row (`add_loras`), replacing the last pick's. Null is "No one".
 */
function pickPerson(id) {
  if (fixedPerson.value) return;
  const person = personFits.value.people.find((entry) => entry.id === id);
  personId.value = person ? person.id : null;
  addedLoras.value = addedLoras.value.filter((row) => !row.person);
  if (person) addedLoras.value.unshift(addedRow(person.loras[0].sha256, 1, true));
}

// Another workflow may be for another base model: a pick that no longer fits
// is taken off rather than sent, and one whose LoRA changed takes the new one.
watch(personFits, (fits) => {
  if (personId.value == null) return;
  const person = fits.people.find((entry) => entry.id === personId.value);
  const row = addedLoras.value.find((entry) => entry.person);
  if (!person || !person.loras.some((lora) => lora.sha256 === row?.sha256)) {
    pickPerson(person ? person.id : null);
  }
});

const setOptions = computed(() => [
  { value: "", label: "No set" },
  ...entityLists.pictureSets.map((row) => ({
    value: String(row.id),
    label: row.name,
  })),
]);

/**
 * Where the output is filed, said as it will actually happen.
 *
 * The run carries the grid's own view context, so with a set in view the new
 * pictures land in it. With none there is nothing to be next to: they are
 * imported into the library unfiled, which is worth saying rather than
 * promising they will appear beside their source.
 */
const destinationLine = computed(() => {
  if (loraSource.value?.entityType === "character") {
    return `${loraSource.value.name}'s reference pictures`;
  }
  const setId = props.context?.set_id;
  const row = entityLists.pictureSets.find(
    (item) => String(item.id) === String(setId),
  );
  if (row) return `${row.name}, beside the pictures they came from`;
  return "Your library, in no set";
});

const fellBackLine = computed(
  () =>
    `${fellBack.value.join(", ")} went back to this workflow's own ${
      fellBack.value.length === 1 ? "value" : "values"
    }.`,
);

/** A seed is a whole number, up to ComfyUI's 2**64-1, checked as digits. */
const seedError = computed(() => {
  if (seedMode.value !== "fixed") return "";
  const text = String(seed.value ?? "").trim();
  if (!/^\d+$/.test(text)) return "A seed is a whole number.";
  return BigInt(text) > MAX_SEED ? "That is larger than ComfyUI's biggest seed." : "";
});

const runBlocker = computed(() => {
  if (!activeKey.value) return "Choose a workflow first.";
  if (seedError.value) return seedError.value;
  if (!Number.isInteger(count.value) || count.value < 1 || count.value > MAX_COUNT)
    return `Between 1 and ${MAX_COUNT} runs at a time.`;
  if (preflightError.value) return preflightError.value;
  if (addedLoras.value.some((row) => !row.sha256)) {
    return "Choose a LoRA for each added row, or remove it.";
  }
  // Before the generic refusal: an empty slot is "no picture yet", said in
  // the words of the slot, and its fix is right there in the Pictures rows.
  if (unfilledInputs.value.length) {
    const titles = unfilledInputs.value.map(inputTitle).join(", ");
    return `Choose a picture for ${titles} first.`;
  }
  if (reasonsBlock(reasons.value)) return "This run cannot start; see below.";
  return "";
});
const canRun = computed(() => !runBlocker.value && !submitting.value);
/**
 * The refusals drawn as notices. `picture_input_unfilled` is not one of them:
 * the Pictures section draws that as an empty slot, and a second, louder copy
 * of it under the form would make a pin whose picture has gone read as an
 * error when it is only a choice not made yet (decision 7).
 */
const shownRefusals = computed(() =>
  reasons.value.filter((reason) => reason?.code !== PICTURE_INPUT_UNFILLED),
);
/** One list on screen: the refusals first, then what the run will do anyway. */
const runNotes = computed(() => [...shownRefusals.value, ...bypassed.value]);

/** Whether the last pre-flight said the run changes nodes a copy could keep. */
const changedNodes = ref(false);
const savingFixed = ref(false);
/** What the last Save fixed workflow said, success or failure. */
const fixedNote = ref("");
const canSaveFixed = computed(() => changedNodes.value && Boolean(activeKey.value));

/**
 * Save fixed workflow: a copy with this ComfyUI's repairs applied, and the
 * popup moved onto it, so this run and the next ones run the copy as saved.
 * The run's own form (prompt, values, LoRAs) is not written into it.
 */
async function saveFixed() {
  if (!canSaveFixed.value || savingFixed.value) return;
  savingFixed.value = true;
  fixedNote.value = "";
  // The popup can move while the copy is written (another workflow picked, or
  // closed and reopened on another source): the answer is then about a card
  // nobody is looking at, and must not pull the popup onto its copy.
  const token = loadToken;
  const savedFrom = activeKey.value;
  const stillHere = () => token === loadToken && savedFrom === activeKey.value;
  try {
    const saved = await saveFixedWorkflow(savedFrom);
    if (!stillHere()) return;
    const note = `Saved as ${String(saved?.name || "").replace(/\.json$/i, "")}.`;
    if (saved?.workflow_id) {
      if (props.source?.pickWorkflow) {
        cards.value = (await listWorkflowCards()).cards;
        if (!stillHere()) return;
      }
      // After the switch, which clears what was said about the card left.
      workflowId.value = saved.workflow_id;
    }
    fixedNote.value = note;
  } catch (err) {
    if (stillHere()) {
      fixedNote.value = errorMessage(err, "Could not save the fixed workflow.");
    }
  } finally {
    savingFixed.value = false;
  }
}

/** The inputs nothing fills, which is what keeps the Run button back. */
const unfilledInputs = computed(() =>
  pictureInputs.value.filter((input) => input.fill === null),
);
/** Whether a selected picture is fed into the graph, per the server. */
const feedsSelection = computed(() =>
  pictureInputs.value.some((input) => input.fill === "selection"),
);
/** Whether "stack with the ones they came from" would be true of this run. */
const stackOffered = computed(
  () =>
    pictureIds.value.length === 1 ||
    (pictureIds.value.length > 1 && feedsSelection.value),
);
/**
 * Whether a setup gesture may start: nothing in flight, and the rows on screen
 * read for the card a write would go to.
 */
const setupReady = computed(
  () =>
    !submitting.value &&
    !inputsBusy.value &&
    !preflighting.value &&
    Boolean(activeKey.value) &&
    inputsKey.value === activeKey.value,
);
const stack = computed({
  get: () => stackChoice.value ?? feedsSelection.value,
  set: (value) => {
    stackChoice.value = Boolean(value);
  },
});

/**
 * What a row is called. A loader with no title of its own is called by its
 * class, and two of those side by side would read as one input twice, so a
 * repeated name is numbered in the order the card lists them.
 */
function inputTitle(input) {
  const title = input?.title || "Picture";
  const same = pictureInputs.value.filter(
    (row) => (row.title || "Picture") === title,
  );
  if (same.length < 2) return title;
  const position = same.findIndex((row) => address(row) === address(input));
  return `${title} ${position + 1}`;
}

/** The line under a row's title: how this run fills it, in words. */
function inputLine(input) {
  const many = pictureIds.value.length;
  switch (input.fill) {
    case "selection":
      return many === 1
        ? "The picture this run starts from"
        : `Your selection: ${many} pictures, one run each`;
    case "request":
      return "Chosen for this run";
    case "fixed":
      return "Kept for every run of this workflow";
    case "graph":
      return "As the workflow has it";
    default:
      return input.picture_missing
        ? "The picture you kept here is gone. Choose another."
        : "No picture yet";
  }
}

function openPicker(input) {
  if (!setupReady.value) return;
  pickerFor.value = input;
}

/**
 * Put focus back on a row after a gesture that removed the control holding it
 * (the clear, the move), so a keyboard user is not dropped to the page.
 */
function focusRow(key) {
  return focusLater(`[data-input="${key}"]`);
}

function clearPicks() {
  for (const key of Object.keys(picks)) delete picks[key];
}

/** The picker answered: this picture fills that slot, for this run. */
async function onPicked(picture) {
  const input = pickerFor.value;
  pickerFor.value = null;
  if (!input || !picture?.id) return;
  picks[address(input)] = {
    slot_label: input.slot_label,
    input_name: input.input_name,
    picture_id: picture.id,
  };
  await settle(runPreflight());
}

async function clearPick(input) {
  if (!setupReady.value) return;
  const key = address(input);
  delete picks[key];
  await settle(runPreflight());
  await focusRow(key);
}

/** Hold the setup controls until *step* - a write and its re-read - is done. */
async function settle(step) {
  inputsBusy.value = true;
  try {
    return await step;
  } finally {
    inputsBusy.value = false;
  }
}

/**
 * The card's whole setup as it stands, with one row changed.
 *
 * Every other row goes back exactly as it was read, pins by the content they
 * were stored with, so a write about one slot can never unpin another. The
 * one exception is a second Selection: a card has at most one, so moving it
 * here makes the old one a picker.
 */
function setupWith(input, change) {
  return pictureInputs.value.map((row) => {
    const entry = {
      slot_label: row.slot_label,
      input_name: row.input_name,
      mode: row.mode,
      pixel_sha: row.pixel_sha || null,
    };
    if (address(row) === address(input)) return { ...entry, ...change };
    if (change.mode === "selection" && row.mode === "selection") {
      return { ...entry, mode: "picker" };
    }
    return entry;
  });
}

async function writeSetup(entries) {
  inputsError.value = "";
  // Asked again here and not only by the controls: this is the one place a
  // whole-set write is sent, so it is the one place the guard cannot be
  // skipped by a caller that forgot to check.
  if (inputsKey.value !== activeKey.value) {
    inputsError.value = "Still reading this workflow's pictures; try again.";
    return false;
  }
  try {
    await setWorkflowInputs(activeKey.value, entries);
    return true;
  } catch (err) {
    inputsError.value = errorMessage(err, "Could not keep that for this workflow.");
    return false;
  }
}

/**
 * Pin or unpin the picture a row shows.
 *
 * Pinning writes it as the card's `fixed` input, by id - the server stores its
 * content. Unpinning keeps the picture for THIS run, so taking the pin off
 * never empties a slot the owner was about to run with.
 */
async function togglePin(input) {
  if (!setupReady.value) return;
  const key = address(input);
  const pinning = input.fill !== "fixed";
  await settle(
    (async () => {
      const ok = await writeSetup(
        setupWith(
          input,
          pinning
            ? { mode: "fixed", picture_id: input.picture_id, pixel_sha: null }
            : { mode: "picker" },
        ),
      );
      if (!ok) return;
      if (pinning) {
        delete picks[key];
      } else {
        picks[key] = {
          slot_label: input.slot_label,
          input_name: input.input_name,
          picture_id: input.picture_id,
        };
      }
      await runPreflight();
    })(),
  );
}

/**
 * Send the selection to this input, and remember that on the card.
 *
 * Remembered rather than per run (the plan's call on its open question 12):
 * it only arises on a card with two or more inputs and nothing pinned, and
 * asking again on every run of such a card is the cost that would be paid.
 */
async function useSelectionHere(input) {
  if (!setupReady.value) return;
  const key = address(input);
  await settle(
    (async () => {
      const ok = await writeSetup(setupWith(input, { mode: "selection" }));
      if (!ok) return;
      delete picks[key];
      await runPreflight();
    })(),
  );
  await focusRow(key);
}
/**
 * The button's number, from the server where it has answered.
 *
 * `RunPreflight.runs` is what `_plan` would actually submit for this body;
 * `count` is only what this form asked for. They agree on the one-group `target`
 * path, and the server is the one to believe when they do not.
 */
const runLabel = computed(() => {
  if (!Number.isInteger(count.value) || count.value < 1) return "Run";
  const planned = plannedRuns.value || count.value;
  return `Run ${planned}`;
});

/** The body both the pre-flight and the run take, so the two never disagree. */
function runBody() {
  // **Every parameter the form displays, not only the edited ones.**
  //
  // The run does NOT start from what this popup is showing: `_plan` builds the
  // graph from `resolve_source` - the imported file, the card's best-scored
  // picture, or the best stored instance - and then applies `body.values` and
  // nothing else. A card's `defaults` are a *display* figure (the mode over its
  // best pictures, `card_defaults`), and the opened picture's own settings are
  // a fact about that picture; neither reaches the graph on its own. Sending
  // only `edits` therefore ran a graph that disagreed with the form: open a
  // picture made at 45 steps on a card whose best picture used 20, press Run
  // untouched, and the form said 45 while the run did 20.
  //
  // Safe to send the lot: `_apply_addressed` leaves a wired input alone (the
  // run's size only when it equals what the wire carries, which is why the
  // picture's recipe reports the size its latent is made at) and "an input the graph
  // does not have is not invented", so an address this graph lacks is inert
  // rather than an error.
  const values = displayedValues();
  const body = {
    // `null` means "leave the graph's own text alone"; `""` means "blank it",
    // and `apply_prompts` honours both literally. A card or a multi-picture
    // selection reads no recipe, so until the pre-flight has read the graph's
    // own prompt the box is empty because there was nothing to prefill it with - sending that emptiness would wipe every positive
    // prompt node in the graph and generate from no prompt at all.
    prompt: promptOverride.value,
    negative: baseNegative.value && !negativeUnset.value ? negative.value : null,
    // Only the rows that DIFFER from the graph. An untouched slot needs no
    // override - ComfyUI loads what the graph already names - and a slot the
    // shelf could not name has no digest to send, which `RunLora.sha256`
    // requires. Sending those back would refuse the run over a LoRA nobody
    // touched.
    loras: changedLoras.value,
    values,
    // Only the pictures picked for this run: a pin, the stored selection and
    // the one open input a selection fills are all the server's to apply, so
    // an empty list is the ordinary body.
    inputs: Object.values(picks),
    stack: stack.value,
    count: count.value,
    seed_mode: seedMode.value,
    // The digits as they were typed. A Number here would round a 64-bit
    // seed into a different one on the way out.
    seed: seedMode.value === "fixed" ? String(seed.value).trim() : null,
    // Only when something is listening: a `client_id` naming a socket nobody
    // reads makes the run look followable when it is not. Only the grid puts
    // one in `context`; the Workflows view's runner follows without one.
    client_id: runDialog.hasRunner ? props.context?.client_id || null : null,
  };
  // Exactly one source, which the route checks before it reads anything: a
  // saved recipe names its own card, with pictures the card is `target`, and
  // without either it IS the source.
  // The graph's own loaders the owner skipped for this run. Only when there
  // are any: the route rewires around each, and a key it is not sent cannot
  // refuse a run over a feature nobody used.
  if (skippedLoras.value.length) body.skip_loras = skippedLoras.value;
  // LoRAs added in loaders of their own: only when there are any.
  if (addLorasBody.value.length) body.add_loras = addLorasBody.value;
  // The same for the stages the owner turned off (#1623).
  if (skippedStages.value.length) body.skip_stages = skippedStages.value;
  // The checkpoint row, by the default recipe's loader address (#1623).
  if (runModels.value.length) body.models = runModels.value;
  if (choiceFixes.value.length) {
    body.choices = choiceFixes.value.map((fix) => ({
      node_id: fix.node_id,
      field: fix.field,
      value: fix.chosen,
    }));
  }
  if (savedRecipe.value) {
    body.saved_recipe_id = savedRecipe.value.id;
    // `target` is the workflow the picker chose, whatever named the group.
    body.target = activeKey.value;
  } else if (pictureIds.value.length) {
    body.picture_ids = pictureIds.value;
    body.target = activeKey.value;
  } else {
    body.workflow_id = activeKey.value;
  }
  const setId = picksDestination.value ? destinationSetId.value : props.context?.set_id;
  if (loraSource.value?.entityType === "character") {
    // The person the popup was opened on, never the view behind it.
    body.destination = {
      set_id: null,
      project_id: null,
      character_id: Number(loraSource.value.entityId),
    };
    return body;
  }
  const destination = {
    set_id: setId ? Number(setId) : null,
    project_id: picksDestination.value ? null : props.context?.project_id ?? null,
    character_id: picksDestination.value
      ? null
      : props.context?.character_id ?? null,
  };
  if (Object.values(destination).some((value) => value != null)) {
    body.destination = destination;
  }
  return body;
}

/**
 * Every parameter as the form currently shows it, addressed for the run.
 *
 * A `null` default is dropped rather than sent: `RunValue.value` is
 * `bool | int | float | str` and a null is a 422 on the whole request. A card
 * default with no value is a parameter nobody has a value for, so there is
 * nothing to override it with.
 */
function displayedValues() {
  const rows = [];
  for (const field of defaults.value) {
    const value = runValue(field);
    if (value === null || value === undefined) continue;
    rows.push({
      slot_label: field.slot_label,
      input_name: field.input_name,
      value,
    });
  }
  return rows.slice(0, MAX_VALUES);
}

/**
 * Add a LoRA row: into a free loader of the graph where there is one, else as
 * a LoRA the run adds in a loader of its own (`add_loras`), so the button
 * works on a workflow with no loader, or none free.
 */
function addLora() {
  const used = new Set(loras.value.map((row) => `${row.field}@${row.node_id}`));
  const slot = loraSlots.value.find(
    (item) => !used.has(`${item.field}@${item.node_id}`),
  );
  if (slot) {
    loras.value.push(loraRow(slot, { added: true }));
    return;
  }
  addedLoras.value.push(addedRow(""));
}

/** `person` marks the row holding the LoRA of the person the run is of. */
function addedRow(sha256, strength = 1, person = false) {
  addedKey += 1;
  return { key: `added-${addedKey}`, sha256, strength, person };
}

function removeAddedLora(index) {
  // Taking the picked person's LoRA off is picking no one.
  if (addedLoras.value[index]?.person) personId.value = null;
  addedLoras.value.splice(index, 1);
}

/**
 * One LoRA row, with the graph's own file resolved to a shelf digest.
 *
 * `baseSha` / `baseStrength` are what the GRAPH has. A row equal to them is
 * not sent at all, which is how a slot the shelf cannot name still works: the
 * run carries no override for it and ComfyUI loads what the graph already
 * says. Only a row the owner actually changed becomes a `RunLora`.
 */
function loraRow(slot, { added = false } = {}) {
  const graphValue = String(slot.value ?? "");
  const sha256 =
    slot.by === "digest" ? graphValue : shelfDigestFor(graphValue);
  const strength = Number(slot.strengths?.model ?? 1);
  return {
    key: `${slot.field}@${slot.node_id}`,
    node_id: String(slot.node_id),
    field: String(slot.field),
    by: String(slot.by || "filename"),
    graphValue,
    added,
    skipped: false,
    baseSha: sha256,
    baseStrength: strength,
    sha256,
    strength,
  };
}

/**
 * The shelf digest for a filename a graph names, or `""`.
 *
 * The same two tiers `apply_adapter` matches on and in the same order - the
 * whole recorded name, then the basename - because ComfyUI counts from its
 * `loras` folder and the shelf from whatever folder it scanned. Matched on
 * exactly one row: two files of that name on the shelf is not a resolution,
 * it is a coin toss over which one the run would load.
 */
function shelfDigestFor(filename) {
  const wanted = filename.trim().toLowerCase();
  if (!wanted) return "";
  const base = wanted.split(/[\\/]/).pop();
  for (const key of [wanted, base]) {
    const hits = adapters.value.filter((adapter) => {
      const name = String(adapter.filename || "").toLowerCase();
      return adapter.sha256 && (name === key || name.split(/[\\/]/).pop() === key);
    });
    if (hits.length === 1) return String(hits[0].sha256);
  }
  return "";
}

/** A row's strength, or 1 when it has none: a 0 is a strength, not a gap. */
function strengthOr1(value) {
  if (value === null || value === undefined || value === "") return 1;
  const number = Number(value);
  return Number.isFinite(number) ? number : 1;
}

/**
 * The files the last pre-flight says the run leaves out on its own, because
 * this ComfyUI does not have them (`bypassed_loras`, `requested: false`).
 */
const bypassedFiles = computed(
  () =>
    new Set(
      bypassed.value
        .filter((note) => note.code === LORAS_BYPASSED)
        .flatMap((note) => note.models || [])
        .map((model) => loraBase(model?.file)),
    ),
);

/** What a row is called: the shelf's name, or the graph's file. */
function rowName(row) {
  if (row.sha256) {
    const shelf = adapters.value.find((item) => item.sha256 === row.sha256);
    const named = shelf?.display_name || shelf?.filename;
    if (named) return loraStem(named) || named;
  }
  return String(row.graphValue || "").split(/[\\/]/).pop() || "LoRA";
}

/**
 * The sentence under a graph row that will not load as written, or "".
 *
 * The pre-flight's word first — the run already leaves that loader out — and
 * then the shelf's, which cannot identify the file. The row's Skip is the fix
 * either way: the owner can run without it on purpose.
 */
function loraFlag(row) {
  if (row.added || row.skipped) return "";
  const file = loraBase(row.graphValue);
  if (file && bypassedFiles.value.has(file)) {
    return "Not on this ComfyUI. The run leaves this loader out.";
  }
  if (!row.baseSha && row.graphValue && row.sha256 === row.baseSha) {
    return "Not on your model shelf: PixlStash cannot identify this file.";
  }
  return "";
}

/** What the LoRA rows' live region is saying, or "". */
const loraLive = ref("");

function focusLoraRow(key, which) {
  return focusLater(`[data-lora="${key}"] [data-focus="${which}"]`);
}

/**
 * Skip one of the GRAPH's loaders for this run (`skip_loras`).
 *
 * Nothing about the workflow changes: the row stays, says it is skipped, and
 * Use takes it back. The pre-flight is asked again, because the answer
 * changes: a loader nothing can be rewired around comes back as
 * `lora_not_skippable`, and a bypassed one is reported as skipped instead.
 */
async function skipGraphLora(row) {
  if (row.added || submitting.value) return;
  row.skipped = true;
  loraLive.value = `${rowName(row)} is skipped for this run. Use is on the same row.`;
  await focusLoraRow(row.key, "use");
  await runPreflight();
}

async function useGraphLora(row) {
  row.skipped = false;
  loraLive.value = `${rowName(row)} is used in this run again.`;
  await focusLoraRow(row.key, "skip");
  await runPreflight();
}

/**
 * A row's options, with the graph's own unresolvable file among them.
 *
 * Without its current value in the list the select would render empty and read
 * as a slot nobody has filled, when in fact the graph fills it with a file the
 * shelf has never seen.
 */
function optionsFor(row) {
  if (row.baseSha || !row.graphValue || row.added) return adapterOptions.value;
  return [
    { value: "", label: `${row.graphValue} (not on your shelf)` },
    ...adapterOptions.value,
  ];
}

function removeLora(index) {
  loras.value.splice(index, 1);
}

/**
 * The `no_lora_loader` fix: take the LoRA out and ASK AGAIN.
 *
 * Without the second half the reason stays in `reasons`, `runBlocker` stays
 * set and the Run button stays disabled for ever - a fix button that makes the
 * refusal permanent. The server decides `wants_lora` from `body.loras`, so an
 * empty list genuinely clears it.
 */
async function dropLoras() {
  loras.value = [];
  addedLoras.value = [];
  personId.value = null;
  await runPreflight();
}

/**
 * The `loras_unplaced` fix: Edit LoRAs… on the card that has no loader left.
 *
 * Adding a loader is a workflow edit, saved as a new workflow, so this popup
 * closes and the Workflows screen opens on that card with the dialog up.
 * Nothing is run.
 */
function editLoras(workflowId) {
  const key = workflowId || activeKey.value;
  if (!key) return;
  emit("close");
  void router?.push?.(editLorasRoute(key));
}

/**
 * "Open in Workflows": the card this popup runs, selected on the Workflows
 * screen with its rail open. The popup closes, as it does for Edit LoRAs…,
 * because the screen it opens on is behind it.
 */
function openInWorkflows() {
  if (!activeKey.value || submitting.value) return;
  const key = activeKey.value;
  emit("close");
  void router?.push?.({ name: "workflows", query: { workflow: key } });
}

/**
 * Create with LoRA: the LoRAs attached to the person or set, and the workflow
 * sets that rank the picker. The first attached LoRA starts as an added row.
 * Read as `AdapterTray` reads them: both attachable file kinds, since the
 * route's `file_kind` takes one.
 */
async function loadLoraSource() {
  const source = loraSource.value;
  const filter = source.entityType === "character" ? "characterId" : "setId";
  const [kinds, sets] = await Promise.all([
    Promise.all(
      ["adapter", "unknown"].map((fileKind) =>
        listAdapters({ fileKind, [filter]: Number(source.entityId) }),
      ),
    ),
    // A set only ranks the list; without it every workflow is still offered.
    fetchWorkflowSets().catch((err) => {
      console.warn("Workflow sets unavailable; ranking without them.", err);
      return { hand_made: [] };
    }),
  ]);
  attachedLoras.value = kinds.flat().filter((row) => row?.sha256);
  handMadeSets.value = sets?.hand_made || [];
  const first = attachedLoras.value[0];
  addedLoras.value = first
    ? [addedRow(first.sha256, 1, source.entityType === "character")]
    : [];
}

/** The picker's two reads, neither of which holds the popup up. */
async function loadPeople() {
  void entityLists.refresh("characters");
  try {
    unknownLoras.value = await listAdapters({ fileKind: "unknown" });
  } catch (err) {
    // The picker then offers the people whose LoRA the shelf has classed.
    console.warn("Could not read the model shelf's unclassified files:", err);
  }
}

async function loadAdapters() {
  if (adapters.value.length) return;
  try {
    adapters.value = await listAdapters();
  } catch (err) {
    // The picker degrades to the slots already in the graph; the run still
    // works, so this is a warning and not a blocker.
    console.warn("Could not read the model shelf's LoRAs:", err);
  }
}

/**
 * Load the card behind `key`, keeping every edit whose address it still has.
 *
 * The fields that do NOT survive are named rather than dropped in silence:
 * switching workflows is a deliberate comparison, and a steps value that
 * quietly went back to 8 is the thing the design asks to be told about.
 */
async function loadCard(key, { keepEdits = false } = {}) {
  // A popup reopened on another source while this read was out has its own
  // card; this one's answer must not replace it.
  const token = loadToken;
  const detail = await getWorkflowCard(key);
  if (token !== loadToken) return;
  const next = detail?.card || null;
  pins.value = detail?.pins ?? null;
  modelFixes.value = detail?.model_fixes ?? [];
  freeError.value = "";
  // Stages are the graph's, so a choice made on one workflow says nothing
  // about another's.
  clearStages();
  for (const key2 of Object.keys(drafts)) delete drafts[key2];
  checkpointFix.value = null;
  checkpointAsk += 1;
  if (!keepEdits) {
    card.value = next;
    fellBack.value = [];
    checkpointEdit.value = null;
    return;
  }
  const addresses = new Set((next?.defaults || []).map(address));
  const lost = Object.keys(edits).filter((key2) => !addresses.has(key2));
  fellBack.value = lost.map((key2) => editedLabels[key2] || key2);
  for (const key2 of lost) {
    delete edits[key2];
    delete editedLabels[key2];
  }
  // A chosen checkpoint survives only onto a loader of the same address, and
  // never onto a two-file card, whose row is read-only and sends no pick.
  const nextCheckpoint = (next?.default_recipe?.models || []).find(
    (model) => model.kind === "checkpoint",
  );
  if (
    checkpointEdit.value !== null &&
    (nextCheckpoint?.address !== checkpointModel.value?.address ||
      baseModelFiles(next).length > 1)
  ) {
    fellBack.value = [...fellBack.value, "Checkpoint"];
    checkpointEdit.value = null;
  }
  card.value = next;
}

async function runPreflight(token = loadToken) {
  const mine = () => token === loadToken;
  if (!activeKey.value) {
    reasons.value = [];
    bypassed.value = [];
    return;
  }
  preflighting.value = true;
  preflightError.value = "";
  const askedFor = activeKey.value;
  try {
    const answer = await preflightWorkflowRun(runBody());
    // The popup has moved to another card since this was asked: its answer is
    // about a card nobody is looking at, and its rows must not become the set
    // a pin on the new card is written from.
    if (!mine() || askedFor !== activeKey.value) return;
    const all = (answer?.groups || []).flatMap((group) => group.reasons || []);
    // A sampler or scheduler this ComfyUI lacks is offered its replacement,
    // and asked again with it: the answer that counts is the one about the
    // graph this run would submit. Only a new one re-asks, so a pick the
    // server still refuses stays on screen as its refusal.
    const fresh = all
      .flatMap((reason) =>
        reason?.code === MISSING_CHOICES ? reason.choices || [] : [],
      )
      .filter(
        (choice) =>
          !choiceFixes.value.some(
            (fix) => fix.node_id === choice.node_id && fix.field === choice.field,
          ),
      );
    if (fresh.length) {
      choiceFixes.value = [
        ...choiceFixes.value,
        ...fresh.map((choice) => ({ ...choice, chosen: choice.replacement })),
      ];
      await runPreflight(token);
      return;
    }
    reasons.value = all;
    offerCheckpoints(all, askedFor, token).catch((err) =>
      console.warn(`[run] could not offer checkpoints for ${askedFor}`, err),
    );
    bypassed.value = (answer?.groups || []).flatMap((group) => [
      ...repairNotices(group),
      ...unplacedNotice(group),
    ]);
    changedNodes.value = (answer?.groups || []).some(changesNodes);
    plannedRuns.value = Number(answer?.runs) || 0;
    // One group: this popup always runs one workflow (`target`, an id or a saved
    // recipe), so the first group's inputs are the card's.
    pictureInputs.value = answer?.groups?.[0]?.picture_inputs || [];
    graphPrompt.value = answer?.groups?.[0]?.prompt || null;
    inputsKey.value = askedFor;
  } catch (err) {
    // The route answers 400/404/422 here exactly as it does on the run, "so
    // the two never disagree" - so a 4xx is this body being refused and is
    // shown now rather than after the owner presses Run. Anything else (the
    // network, a 5xx) is the question not being asked, which is not a refusal:
    // the button stays live and the run itself answers.
    if (!mine()) return;
    reasons.value = [];
    bypassed.value = [];
    changedNodes.value = false;
    graphPrompt.value = null;
    // What the card's inputs are is no longer known, so nothing may be
    // written from the last answer: that is how a pin reverted the one before.
    pictureInputs.value = [];
    inputsKey.value = "";
    const status = err?.response?.status;
    if (status >= 400 && status < 500) {
      preflightError.value = errorMessage(err, "This run would be refused.");
    } else {
      preflightError.value = "";
      console.warn("Could not pre-flight this run:", err);
    }
  } finally {
    if (mine()) preflighting.value = false;
  }
}

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  // Everything else starts empty: App.vue mounts a fresh popup per source.
  loading.value = true;
  // The caller's own checkbox, explicit false included; else the popup decides.
  stackChoice.value = props.source?.stack ?? null;
  try {
    if (props.source?.pickWorkflow) {
      cards.value = (await listWorkflowCards()).cards;
    }
    if (loraSource.value) {
      await loadLoraSource();
      if (!mine()) return;
    }
    // One picture is a recipe to prefill from; several are a card the server
    // already agreed they share, so the card alone is the honest source.
    if (pictureIds.value.length === 1 && !isEdit.value) {
      const data = await getPictureRecipe(pictureIds.value[0], { preflight: false });
      if (!mine()) return;
      recipe.value = data?.reason === "no_prompt_chunk" ? null : data;
    }
    const key =
      props.source?.workflowId ||
      recipe.value?.workflow_id ||
      // Create with LoRA opens on the best fit, not on an empty picker.
      (loraSource.value ? workflowOptions.value[0]?.value : "") ||
      "";
    activeKey.value = key;
    if (key) await loadCard(key);
    if (!mine()) return;
    prompt.value = props.source?.emptyPrompt ? "" : basePrompt.value;
    negative.value = baseNegative.value;
    seed.value = seedText.value || "0";
    // EVERY slot the graph carries, not only the digest ones.
    //
    // `by: "digest"` is PixlStash's own loader node; every stock `LoraLoader`
    // names its file in a `lora_name` widget and is `by: "filename"`, so
    // filtering on digest showed no LoRAs at all on any ordinary workflow.
    // A filename is resolved against the shelf the way the backend's own
    // `apply_adapter` does it - exact, then basename - and a slot the shelf
    // cannot name is still SHOWN, because it is a fact about the graph; it
    // simply has no digest to send, which is exactly what leaving it untouched
    // means anyway.
    await loadAdapters();
    if (offersPerson.value) void loadPeople();
    loras.value = loraSlots.value.map((slot) => loraRow(slot));
    initialLoraCount.value = loras.value.length;
    addedAtOpen.value = addedSignature.value;
    applySavedRecipe();
    destinationSetId.value =
      loraSource.value?.entityType === "set"
        ? String(loraSource.value.entityId)
        : readLastSet() ||
          (props.context?.set_id ? String(props.context.set_id) : "");
    // Both branches need the names: one to pick a set, the other to say which
    // one the output is going into.
    void entityLists.refresh("sets");
    await runPreflight();
  } catch (err) {
    if (mine()) {
      loadFailed.value = errorMessage(err, "Could not read what this would run.");
    }
  } finally {
    if (mine()) loading.value = false;
  }
}

/**
 * Show the saved recipe this popup was opened on.
 *
 * Each override is written into `edits` rather than merely displayed, because
 * `runBody` sends every parameter the form shows: a recipe value left out of
 * `edits` would be sent as the card's own default and the recipe's own value
 * would never reach the graph.
 *
 * LoRAs are deliberately NOT written here. A saved LoRA names a file and a
 * strength but no slot, and the slot only exists once the graph is resolved;
 * the route fills them in itself when the body carries none, which is what
 * this form sends when it was opened on a card with no picture behind it.
 *
 * **That is all-or-nothing on the route's side**: `body.loras` non-empty makes
 * the request's rows the whole LoRA set and the recipe's are not merged under
 * them. So editing one row of a saved recipe's LoRAs in this form replaces the
 * lot. It only arises once a card with no picture behind it shows LoRA rows at
 * all, which needs the card's own slots (`recipe.lora_slots` is the picture's).
 */
function applySavedRecipe() {
  const row = savedRecipe.value;
  if (!row) return;
  prompt.value = row.prompt || "";
  negative.value = row.negative || "";
  const byAddress = new Map(defaults.value.map((field) => [address(field), field]));
  for (const [key, value] of Object.entries(row.overrides || {})) {
    const field = byAddress.get(key);
    // An address this card does not carry is left alone rather than invented:
    // the recipe may have been saved on another workflow.
    if (!field) continue;
    setValue(field, value);
  }
  // A kept seed is shown as the chosen seed, so the form both says what the
  // recipe does and sends it: `POST /workflows/run` lets a request that names
  // `seed_mode` win over the row, and this form always names one.
  if (row.keep_seed && row.seed != null && row.seed !== "") {
    seedMode.value = "fixed";
    seed.value = String(row.seed);
  }
}

function readLastSet() {
  try {
    return window.localStorage?.getItem(LAST_SET_KEY) || "";
  } catch {
    return "";
  }
}

function rememberSet(value) {
  try {
    window.localStorage?.setItem(LAST_SET_KEY, value || "");
  } catch {
    // The preference simply will not persist this session.
  }
}

function onRequestClose() {
  if (submitting.value) return;
  emit("close");
}

/**
 * Escape closes this popup.
 *
 * A persistent dialog suppresses `AppDialog`'s own Escape, so the close is made
 * here. Saving a recipe is its own dialog now and answers its own Escape from
 * its own teleported subtree, so there is no nested mode in this footer left
 * to back out of first.
 */
function onEscape(event) {
  if (submitting.value) return;
  event.stopPropagation();
  emit("close");
}

async function submit() {
  if (!canRun.value) return;
  submitting.value = true;
  submitError.value = "";
  try {
    // For the Edit tab that opened this: who was in the stack BEFORE the run,
    // so the member it adds can be told apart once it is imported.
    const sourceId = pictureIds.value[0];
    let beforeIds = new Set([String(sourceId)]);
    if (props.source?.fromEditTab && stack.value) {
      try {
        beforeIds = (await stackMemberIds(sourceId)).ids;
      } catch (err) {
        // Unknown, not just the source: guessing would offer an older stack
        // member as this run's result.
        console.warn(
          `Could not read the stack of picture ${sourceId} before its run, so the Edit tab will not point at a result:`,
          err,
        );
        beforeIds = null;
      }
    }
    const answer = await runWorkflowCard(runBody());
    const prompts = Array.isArray(answer?.prompts) ? answer.prompts : [];
    if (!prompts.length) {
      reasons.value = (answer?.groups || []).flatMap((group) => group.reasons || []);
      // The run's own answer, so a slot emptied since the pre-flight (a pin
      // binned in another tab) shows as the empty slot it now is.
      if (answer?.groups?.[0]?.picture_inputs) {
        pictureInputs.value = answer.groups[0].picture_inputs;
        inputsKey.value = activeKey.value;
      }
      submitError.value = shownRefusals.value.length
        ? "Nothing was queued; see the reason below."
        : "Nothing was queued: a picture above still needs choosing.";
      return;
    }
    if (picksDestination.value) rememberSet(destinationSetId.value);
    if (props.source?.fromEditTab) {
      runDialog.editRun = {
        prompts,
        pictureId: pictureIds.value[0],
        workflowName: card.value?.name || "",
        instruction: prompt.value || "",
        stack: stack.value,
        beforeIds,
      };
    }
    const label = taskLabelFor(answer);
    emit("run", {
      prompts: labelPrompts(prompts, () => label),
      pictureIds: pictureIds.value,
    });
    emit("close");
  } catch (err) {
    // A submission error is a FORM error: keep the dialog and every input.
    submitError.value = errorMessage(err, "Could not start the run.");
  } finally {
    submitting.value = false;
  }
}

void load();
</script>

<style scoped>
/* `aria-disabled`, so the button keeps focus and its reason stays reachable -
   but it must not look pressable, or the dead click is a surprise. The same
   dimming the native disabled state uses. */
:deep(.run-refused) {
  opacity: var(--opacity-disabled);
}

.rund {
  display: grid;
  /* 168px is the design's own source column, and the picture below fills it at
     168×252. Nothing else in the app draws a 2:3 thumbnail at a fixed size, so
     it stays here rather than becoming a token with one reader. */
  grid-template-columns: 168px minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

.rund-src {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.rund-pic {
  width: 168px;
  height: 252px;
  border-radius: var(--radius-md);
  object-fit: cover;
  display: block;
}

.rund-pic--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.rund-meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.rund-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.rund-sub,
.rund-kv dt {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.rund-kv {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.rund-kv dd {
  margin: 0;
  font-size: var(--text-sm);
}

.rund-mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  overflow-wrap: anywhere;
}

.rund-form {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-5) var(--space-4);
  align-content: start;
}

.rund-f {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.rund-f--2 {
  grid-column: span 2;
}

.rund-f--3 {
  grid-column: span 3;
}

.rund-f--4 {
  grid-column: span 4;
}

/* The label row carries the ↺ chip, so an edited field never changes height
   and the four columns stay on one baseline. */
.rund-l {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--space-5);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A fieldset only for its legend: no browser border, inset or min-width. */
.rund-stages {
  border: 0;
  margin: 0;
  padding: 0;
  min-width: 0;
}

/* Floated, a legend is no longer the "rendered legend" drawn on the border:
   it becomes a flex item, so `.rund-f`'s gap spaces it like every label row. */
.rund-stages > legend {
  float: left;
  width: 100%;
  padding: 0;
}

.rund-sp {
  flex: 1;
}

/* The footer is one row that does not wrap, so a long recipe name has to be
   allowed to shrink and wrap rather than push the buttons out of it: a flex
   item's `min-width: auto` is what would stop it. */
.rund-kept {
  min-width: 0;
}

/* The "Strength" header has to sit over the strength box, so the two share one
   value: changed apart, the label stops lining up with the column it names. */
.rund-form {
  --rund-strength-w: 72px;
  /* Wide enough for "Skip" and "Use" at the compact button size. */
  --rund-act-w: 56px;
}

.rund-l2 {
  width: var(--rund-strength-w);
  text-align: right;
}

.rund-x-gap {
  width: var(--rund-act-w);
}

.rund-lora,
.rund-size {
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--rund-strength-w) var(--rund-act-w);
  gap: var(--space-3);
  align-items: center;
}

/* Width and height split the cell evenly. At `--rund-f--2` that is one grid
   column each, which is what a five-digit number needs and what the single
   cell never gave it. */
.rund-size {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
}

/* ── Pictures in (#1457) ─────────────────────────────────────────────────
   A row is the tile, the words, then the pin and the clear in the same
   bar-button column the LoRA rows use, so the right edge lines up. The tile is
   72px: the size a picture is still recognisable at in a four-column form, and
   a component-local value for the same reason the 168px source column is. */
.rund-pics {
  gap: var(--space-3);
}

.rund-in {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) var(--control-h-bar) var(--control-h-bar);
  gap: var(--space-4);
  align-items: center;
}

.rund-in-tile {
  width: 72px;
  height: 72px;
  padding: 0;
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

button.rund-in-tile {
  cursor: pointer;
}

button.rund-in-tile:hover:not(.rund-in-tile--off) {
  background: var(--hover-wash);
}

button.rund-in-tile.rund-in-tile--off {
  opacity: var(--opacity-disabled);
  cursor: default;
}

/* An empty slot is drawn as a place to put something, not as a broken image. */
.rund-in-tile--empty {
  border-style: dashed;
}

.rund-in-img,
.rund-in-cell {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* The selection: up to four of its pictures, as a 2 x 2 contact sheet. */
.rund-in-tile--sel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-auto-rows: 1fr;
  gap: var(--space-1);
}

.rund-in-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  min-width: 0;
}

.rund-in-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.rund-in-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rund-in-sub--bad {
  color: rgb(var(--v-theme-surface-error));
}

.rund-in-move {
  margin-top: var(--space-1);
}

.rund-check {
  flex-direction: row;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.rund-check label {
  cursor: pointer;
}

.rund-box {
  width: 16px;
  height: 16px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

/* One live region around the refusals: see RunReasonNotice. */
.rund-reasons {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.rund-fixed {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.rund-more {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-4);
}

.rund-disc > summary {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  font-size: var(--text-sm);
  cursor: pointer;
}

.rund-fixed-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
  padding-top: var(--space-3);
}

.rund-fixed-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rund-quiet,
.rund-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.rund-note {
  margin: 0;
  line-height: var(--leading-body);
}

/* A graph row skipped for this run: its name, struck, the words saying so
   across the select and strength columns, and Use where Skip was. */
.rund-lora--skipped {
  min-height: var(--control-h);
}

.rund-lora-skip-line {
  grid-column: 1 / 3;
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
  font-size: var(--text-sm);
}

.rund-lora-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-decoration: line-through;
}

.rund-lora-skipped {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The last column holds × on an added row and Skip / Use on a graph row, so
   it is sized for the word and the glyph sits at its end. */
.rund-lora-act {
  justify-self: end;
}

.rund-lora-flag {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}

.rund-lora-flag-glyph {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.rund-note--bad {
  color: rgb(var(--v-theme-surface-error));
}

.rund-trigger {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.rund-trigger-glyph {
  flex-shrink: 0;
}
</style>
