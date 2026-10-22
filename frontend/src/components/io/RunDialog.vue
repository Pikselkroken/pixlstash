<template>
  <AppDialog
    open
    size="lg"
    :title="title"
    :subtitle="subtitle"
    :persistent="submitting || dirty"
    @close="close"
    @keydown.esc="onEscape"
  >
    <!-- Focusable, for the one failure that takes the control holding focus
         with it: a save that landed and could not be read again (`keep`). -->
    <p
      v-if="loadFailed"
      :id="loadFailedId"
      ref="loadFailedLine"
      class="rund-note rund-note--bad"
      role="alert"
      tabindex="-1"
    >
      {{ loadFailed }}
    </p>
    <p v-else-if="loading" class="rund-note" role="status">
      Reading what this runs…
    </p>

    <!-- Inert while a band save is out, from its POST until the workflow is
         read again: an edit made then is in neither the save nor the form
         that comes back, so it would be lost without a word. This is every
         control that edits; the band, whose name field keeps the keyboard,
         and the footer are outside it. -->
    <div v-else class="rund" :inert="Boolean(saving) || undefined">
      <!-- ── Left: what the run is made from ──────────────────────────────
           168px wide and the picture 168×252, which is the design's own
           measurement and nothing else in the app uses: a component-local
           value rather than a token nobody else would read. -->
      <div class="rund-src">
        <!-- The workflow's own cover is something it once made, not what this
             run starts from: beside an empty Load Image it was read as the
             chosen picture, so it says so. -->
        <div v-if="coverUrl" class="rund-cover">
          <img
            class="rund-pic"
            :src="coverUrl"
            :alt="
              coverIsExample
                ? `An example of what ${sourceName} makes`
                : `The picture this run is made from: ${sourceName}`
            "
          />
          <span v-if="coverIsExample" class="rund-example" aria-hidden="true">
            Example
          </span>
        </div>
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
          <!-- Who the pictures are of: everyone whose LoRA the run loads, by
               itself, less whom the owner took off (their LoRA still runs),
               plus whom they added (linked, nothing loaded). Sent whole as
               `character_ids`; the tiles on the right still choose LoRAs. -->
          <template v-if="ofRows.length || entityLists.characters.length">
            <dt :id="ofLabelId">Pictures are of</dt>
            <dd class="rund-of">
              <ul class="rund-of-list" :aria-labelledby="ofLabelId" data-testid="rund-of">
                <li v-if="!ofRows.length" class="rund-of-row rund-of-row--none">
                  <span class="rund-of-face" aria-hidden="true">
                    <v-icon size="14">mdi-account-off-outline</v-icon>
                  </span>
                  <span class="rund-of-name">No one</span>
                </li>
                <li
                  v-for="row in ofRows"
                  :key="row.id"
                  class="rund-of-row"
                  :class="{ 'rund-of-row--off': row.off }"
                  :data-of="row.id"
                >
                  <img
                    v-if="!brokenFaces.has(row.id)"
                    class="rund-of-face"
                    :src="characterThumbnailUrl(row.id)"
                    alt=""
                    @error="brokenFaces.add(row.id)"
                  />
                  <span v-else class="rund-of-face" aria-hidden="true">
                    <v-icon size="14">mdi-account-outline</v-icon>
                  </span>
                  <span class="rund-of-text">
                    <span class="rund-of-name">{{ row.name }}</span>
                    <span class="rund-of-why">{{
                      row.off
                        ? "LoRA still runs"
                        : row.byLora
                          ? "by their LoRA"
                          : "linked only"
                    }}</span>
                  </span>
                  <AppButton
                    v-if="row.off"
                    size="sm"
                    variant="ghost"
                    data-focus="undo"
                    :aria-label="`Link the pictures to ${row.name} again`"
                    :disabled="submitting"
                    @click="restorePerson(row)"
                  >
                    Undo
                  </AppButton>
                  <AppBarButton
                    v-else
                    icon="close"
                    data-focus="remove"
                    :tooltip="
                      row.byLora
                        ? `Do not link the pictures to ${row.name}; their LoRA still runs`
                        : `Do not link the pictures to ${row.name}`
                    "
                    :disabled="submitting"
                    @click="removePerson(row)"
                  />
                </li>
              </ul>
              <AppSelect
                v-if="addOptions.length > 1"
                class="rund-of-add"
                model-value=""
                label="Add someone"
                hide-label
                compact
                :options="addOptions"
                :disabled="submitting"
                @update:model-value="addPerson"
              />
              <p v-if="ofPeopleIds.length > 1" class="rund-note">
                Each face goes to whichever of these it looks most like.
              </p>
            </dd>
          </template>
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
              :disabled="submitting || Boolean(saving)"
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
                'rund-in-tile--needed': input.fill === null,
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
            <!-- A pick for this run, the selection sent here over a replay's
                 original included: clearing it is the way back. -->
            <AppBarButton
              v-if="address(input) in picks"
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
            :model-value="prompt"
            label="Prompt"
            :placeholder="isEdit ? 'Describe the change, e.g. make it night time' : ''"
            :rows="3"
            :disabled="submitting"
            :highlight="personTriggers.map((trigger) => trigger.word)"
            @update:model-value="typePrompt"
            @keydown.stop
          />
          <!-- A picked person's LoRA answers to a word the prompt does not
               say. A suggestion, never a blocker: a word's button goes once
               the word is typed, and the box then marks the word instead.
               ONE line whatever is missing: the words do not shrink, the
               buttons do, and past three one button adds them all. Said by
               `triggerLive`, below the form: this line comes and goes. -->
          <p
            v-if="triggerAdds.length"
            class="rund-note rund-note--bad rund-trigger"
            data-testid="rund-trigger"
          >
            <span class="rund-trigger-text">
              <v-icon size="14" aria-hidden="true">mdi-alert-outline</v-icon>
              Trigger word missing
            </span>
            <!-- The label is the word alone and may be cut short, so the name
                 and the tip both say it whole and whose it is. Not the
                 button's own `tooltip`, which would describe it with the
                 words it is already named by. -->
            <Tooltip
              v-for="add in triggerAdds"
              :key="add.label"
              :text="add.hint"
              :describe="false"
            >
              <template #activator="{ props: tip }">
                <AppButton
                  v-bind="tip"
                  class="rund-trigger-add"
                  size="sm"
                  :aria-label="add.hint"
                  :disabled="submitting"
                  @click="addTriggers(add.words)"
                >
                  {{ add.label }}
                </AppButton>
              </template>
            </Tooltip>
          </p>
        </div>

        <!-- Who the run is of. A workflow run by itself carries no person's
             LoRA (the server leaves it out), so the people whose LoRA works
             with its checkpoint are offered here, by face, and several can be
             picked. Opened on a person (Create with LoRA…), that person is
             drawn chosen and fixed, with the others beside them. -->
        <div v-if="showPerson" class="rund-f rund-f--4" data-testid="rund-person">
          <span class="rund-l">Person</span>
          <PersonPicker
            v-if="personTiles.length"
            :people="personTiles"
            :model-value="chosenPersonIds"
            :fixed-id="fixedPerson?.id ?? null"
            :disabled="submitting"
            aria-label="Person"
            @toggle="pickPerson"
          />
          <p v-if="personNote" class="rund-note">{{ personNote }}</p>
        </div>

        <div class="rund-f rund-f--4">
          <span class="rund-l">
            LoRAs<span class="rund-sp" /><span class="rund-l2">Strength</span>
            <span class="rund-x-gap" />
          </span>
          <p v-if="ownLorasLine" class="rund-note">
            {{ ownLorasLine }}
          </p>
          <template v-for="(row, index) in loras" :key="row.key">
          <!-- A loader of the graph taken out with its ×: the run goes
               without it, and so does the workflow if this is saved. The row
               stays, struck through, and Put back takes it back. -->
          <div
            v-if="row.skipped"
            class="rund-lora rund-lora--skipped"
            :data-lora="row.key"
          >
            <span class="rund-lora-skip-line">
              <span class="rund-lora-name">{{ rowName(row) }}</span>
              <span class="rund-lora-skipped">Removed</span>
            </span>
            <AppButton
              class="rund-lora-back"
              size="sm"
              data-focus="back"
              :aria-label="`Put ${rowName(row)} back`"
              :disabled="submitting"
              @click="useGraphLora(row)"
            >
              Put back
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
            <!-- One gesture, ×, on either kind of row. A row the owner ADDED
                 into a free loader goes with it. A loader the GRAPH carries is
                 taken out of this run (`skip_loras`; the server rewires around
                 it) and its row stays, so it can be put back. Moving a loader
                 between lanes is Edit LoRAs…, on the Workflows screen. -->
            <AppBarButton
              class="rund-lora-act"
              icon="close"
              data-focus="remove"
              :tooltip="row.added ? `Remove LoRA ${index + 1}` : `Remove ${rowName(row)}`"
              :disabled="submitting"
              @click="row.added ? removeLora(index) : skipGraphLora(row)"
            />
          </div>
          <!-- A loader set to another LoRA or strength: what the workflow
               has, and the way back to it. -->
          <p
            v-if="loraEdited(row)"
            class="rund-note rund-lora-was"
            data-testid="rund-lora-was"
          >
            <RunResetChip
              :value="loraWas(row)"
              :label="rowName(row)"
              @reset="resetLora(row)"
            />
          </p>
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
               workflow. Their × takes the addition away. The picked people's
               rows come first, in the order picked. -->
          <div
            v-for="(row, index) in addedLoras"
            :key="row.key"
            class="rund-lora"
            :data-lora="row.key"
          >
            <AppSelect
              v-model="row.sha256"
              :label="addedRowName(row, index)"
              hide-label
              compact
              :options="row.person == null ? adapterOptions : personLoraOptions(row)"
              :disabled="submitting"
            />
            <AppInput
              v-model.number="row.strength"
              :aria-label="`Strength of ${addedRowName(row, index)}`"
              type="number"
              min="-10"
              max="10"
              :disabled="submitting"
              @keydown.stop
            />
            <!-- Not on a fixed person's row: the popup was opened to make
                 pictures of them, and taking their LoRA off is another run. -->
            <AppBarButton
              v-if="row.person == null || row.person !== fixedPerson?.id"
              class="rund-lora-act"
              icon="close"
              :tooltip="`Remove ${addedRowName(row, index)}`"
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
            data-focus="add-lora"
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
            {{ parameterTitle(field) }}
            <Tooltip
              v-if="parameterTitle(field) !== field.label"
              :text="field.label"
              activator="parent"
            />
            <!-- A parameter the owner added is named after its setting alone:
                 the node says which of two "text"s this is. -->
            <span v-if="field.exposed && formNodeNames[address(field)]">
              · {{ formNodeNames[address(field)] }}
            </span>
            <RunResetChip
              v-if="isEdited(field)"
              :value="baseOf(field)"
              :label="field.label"
              @reset="resetValue(field)"
            />
          </span>
          <!-- A value ComfyUI lists the choices of (a sampler, an exposed
               drop-down) is picked from them; any other is typed. -->
          <AppSelect
            v-if="choicesFor(formOptions, field, currentValue(field))"
            :model-value="String(currentValue(field))"
            :label="field.label"
            hide-label
            compact
            :options="choicesFor(formOptions, field, currentValue(field))"
            :disabled="submitting"
            data-testid="rund-param-choice"
            @update:model-value="(v) => typeValue(field, v)"
          />
          <AppInput
            v-else
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
            {{ fix.value }} is not in this ComfyUI, so this run uses the one
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
        <p
          v-if="repeatsPicture"
          class="rund-f rund-f--4 rund-note"
          role="status"
          data-testid="rund-repeats-picture"
        >
          Same recipe and same seed, so this makes the picture you already
          have, and an exact duplicate is not added again. Change the seed or a
          setting to get a new one.
        </p>

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

        <!-- The workflow's optional stages (#1623), each ticked as its
             default recipe has it. One switched off is sent as `skip_stages`,
             one switched on as `keep_stages`. -->
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
              :value="shownModel(checkpointBase)"
              label="Checkpoint"
              @reset="pickCheckpoint(checkpointBase)"
            />
          </span>
          <!-- A picker wherever something says what the loader can load: a
               checkpoint this ComfyUI does not have is offered the shelf's of
               the same base model, so the recipe's LoRAs still fit, or every
               one the workflow can load when none is known to be. -->
          <AppSelect
            v-if="checkpointOptions.length"
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

        <!-- The other models the default recipe loads (a VAE, a text
             encoder), sent as `models` like the checkpoint when changed. A
             row is a picker once the server has said what its loader can
             load, and is read as text until then, or where nothing says. -->
        <div
          v-for="row in modelRows"
          :key="row.key"
          class="rund-f rund-f--2"
          data-testid="rund-model"
        >
          <span class="rund-l">
            {{ row.label }}
            <RunResetChip
              v-if="row.key in modelEdits"
              :value="shownModel(row.file)"
              :label="row.label"
              @reset="pickModel(row, row.file)"
            />
          </span>
          <AppSelect
            v-if="modelOptions(row).length"
            :model-value="modelValue(row)"
            :label="row.label"
            hide-label
            compact
            :options="modelOptions(row)"
            :disabled="submitting"
            @update:model-value="(file) => pickModel(row, file)"
          />
          <span v-else class="rund-fixed-value" :title="modelValue(row)">{{
            shownModel(modelValue(row))
          }}</span>
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
          <!-- Pinned for this workflow: every run uses its value, so it is not
               a field here. Read-only, each with the way to free it, which
               writes the workflow's own choice; there is no one-off override
               of a fixed value. -->
          <details
            v-if="fixedFields.length"
            class="rund-disc"
            data-testid="rund-fixed-params"
          >
            <summary ref="fixedSummary">
              Pinned for this workflow
              <span class="rund-count">{{ fixedFields.length }}</span>
            </summary>
            <div
              v-for="field in fixedFields"
              :key="address(field)"
              class="rund-fixed-row"
            >
              <!-- A value kept from another workflow or a saved recipe still
                   overrides a fixed one for this run, so it keeps its ↺. -->
              <span class="rund-l">
                {{ parameterTitle(field) }}
                <Tooltip
                  v-if="parameterTitle(field) !== field.label"
                  :text="field.label"
                  activator="parent"
                />
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

        <p
          v-if="adding.size"
          class="rund-f rund-f--4 rund-note rund-adding"
          role="status"
          data-testid="rund-adding"
        >
          <v-progress-circular indeterminate size="12" width="2" />
          {{
            adding.size === 1
              ? "Found a missing model in your model folders. Adding it to your shelf…"
              : `Found ${adding.size} missing models in your model folders. Adding them to your shelf…`
          }}
        </p>
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
        <p v-if="submitError" class="rund-f rund-f--4 rund-note rund-note--bad" role="alert">
          {{ submitError }}
        </p>
        <p
          v-if="openError"
          class="rund-f rund-f--4 rund-note rund-note--bad"
          role="alert"
          data-testid="rund-open-error"
        >
          {{ openError }}
        </p>
      </div>
    </div>

    <!-- ── Save what you ran (#1850) ────────────────────────────────────
         Only when the form differs from the workflow it runs: what differs,
         counted and named, and the two verbs that keep it. A panel in the
         body and readable at rest, because a count nobody sees is how a
         one-off gets saved by accident. Run never saves, and neither does
         closing. -->
    <template v-if="!loading && !loadFailed">
      <div
        v-if="bandShown"
        ref="bandBox"
        class="rund-band"
        :class="{ 'rund-band--naming': naming }"
        role="group"
        aria-label="Changes from the workflow"
        data-testid="rund-band"
      >
        <!-- Save as new… is this band's one question: the name. Read-only
             while the save is out, never disabled: a disabled field cannot
             hold focus, and a save sent with Enter and refused would leave
             the keyboard nowhere, beside the sentence saying what to fix.
             Dimmed by hand for it, as `disabled` would have. -->
        <template v-if="naming">
          <AppInput
            ref="nameField"
            v-model="newName"
            class="rund-band-name"
            :class="{ 'run-refused': Boolean(saving) }"
            label="Name of the new workflow"
            :readonly="Boolean(saving)"
            @enter="saveAsNew"
            @keydown.esc="backFromName"
            @keydown.stop
          />
          <AppButton
            variant="ghost"
            size="sm"
            :disabled="Boolean(saving)"
            @click="backFromName"
          >
            Back
          </AppButton>
          <AppButton
            ref="bandVerb"
            size="sm"
            :loading="saving === 'new'"
            :aria-disabled="newName.trim() ? undefined : 'true'"
            :class="{ 'run-refused': !newName.trim() }"
            @click="saveAsNew"
          >
            Save as new workflow
          </AppButton>
        </template>
        <template v-else>
          <div class="rund-band-text">
            <b>{{ changesWord(band.count) }} from the workflow</b>
            <span class="rund-band-list" :title="band.text">{{ band.text }}</span>
            <!-- A line of its own, never cut: "why did Save not keep my
                 prompt" is the first question this band invites. -->
            <span class="rund-band-stays">{{ bandStays }}</span>
          </div>
          <AppButton
            ref="asNewButton"
            size="sm"
            :disabled="submitting || Boolean(saving)"
            @click="askName"
          >
            Save as new…
          </AppButton>
          <!-- `aria-disabled`, not `disabled`: a closed Save keeps its place
               in the tab order so the server's reason, which it points at, can
               be reached. `saveOver` does the refusing. -->
          <AppButton
            ref="bandVerb"
            size="sm"
            icon-left="content-save-outline"
            :loading="saving === 'save'"
            :disabled="submitting"
            :class="{ 'run-refused': Boolean(saveClosed) }"
            :aria-disabled="saveClosed ? 'true' : undefined"
            :aria-describedby="saveClosed ? saveReasonId : undefined"
            @click="saveOver"
          >
            Save
          </AppButton>
        </template>
        <p
          v-if="saveClosed && !naming"
          :id="saveReasonId"
          class="rund-note rund-band-why"
          data-testid="rund-band-why"
        >
          {{ saveClosed }}
        </p>
        <p v-if="bandError" class="rund-note rund-note--bad rund-band-why" role="alert">
          {{ bandError }}
        </p>
      </div>
      <!-- What the last save here did, where the band was, and where focus
           goes when the band's own buttons leave with it. -->
      <p
        v-else-if="bandNote"
        ref="bandNoteLine"
        class="rund-note rund-band-done"
        tabindex="-1"
        data-testid="rund-band-done"
      >
        {{ bandNote }}
      </p>
    </template>

    <!-- In the document for as long as the popup is, and only their words
         change: a reader announces a change to a region it already knows, not
         a region that arrives holding its sentence. Each is emptied when what
         it says is no longer so, or the same words coming back change nothing.
         The band's is said once when it arrives, never per key. -->
    <p class="visually-hidden" role="status" aria-live="polite" data-testid="rund-band-live">
      {{ bandLive }}
    </p>
    <p class="visually-hidden" role="status" aria-live="polite" data-testid="rund-trigger-live">
      {{ triggerLive }}
    </p>

    <template #footer>
      <!-- Status, never an alert: it says a gesture is already done, and the
           `--bad` spelling beside a real run refusal would read as a second
           thing wrong. -->
      <p v-if="keptAs" :id="savedReasonId" class="rund-note rund-kept">
        Already kept as “{{ keptAs.name || "Untitled" }}”.
      </p>
      <!-- A wait is the popup's own work and no fault of the form's, so it is
           not said in a refusal's colour. Focusable: it is here for the whole
           of a save, which nothing in the band is, so it is where focus waits
           when a save leaves it nowhere else (`keep`). -->
      <p v-if="runWait" :id="blockerId" ref="runWaitLine" class="rund-note" tabindex="-1">
        {{ runWait }}
      </p>
      <p v-else-if="runBlocker" :id="blockerId" class="rund-note rund-note--bad">
        {{ runBlocker }}
      </p>
      <!-- A look already kept is `aria-disabled`, not `disabled`, for this
           dialog's own stated reason: a natively-disabled button is out of
           the tab order, so a keyboard reader could never reach the sentence
           saying why it is inert. `onSave` refuses that one. With no
           workflow, a form still being read, or a run or a save out, it is
           natively disabled: the recipe is made from the form, so not from
           one that is still being put right. -->
      <AppButton
        :icon-left="keptAs ? 'check' : 'bookmark-plus-outline'"
        :disabled="!activeKey || !formRead || submitting || Boolean(saving)"
        :aria-disabled="keptAs ? 'true' : undefined"
        :aria-describedby="keptAs ? savedReasonId : undefined"
        @click="onSave"
      >
        {{ keptAs ? "Saved" : "Save as recipe" }}
      </AppButton>
      <!-- This run in the owner's ComfyUI: the workflow with what the form
           holds in it, which ComfyUI saves as a NEW workflow. Beside the
           other thing to do with the form that is not running it, and only
           where there is a ComfyUI to open. Disabled as Save as recipe is,
           while there is no form to send; refused with `aria-disabled`, and
           the tooltip saying why, where ComfyUI's nodes cannot open a run or
           the seed is not one. The tooltip otherwise says what a save there
           does, which the label has no room for. -->
      <AppButton
        v-if="canOpenComfyui"
        data-testid="rund-open-comfyui"
        :tooltip="openTip"
        :loading="opening"
        :disabled="!activeKey || !formRead || submitting || Boolean(saving)"
        :class="{ 'run-refused': Boolean(openRefusal) }"
        :aria-disabled="openRefusal ? 'true' : undefined"
        @click="openInComfyui"
      >
        <template #icon="{ size }"><ComfyuiIcon :size="size" /></template>
        Open in ComfyUI
      </AppButton>
      <span class="rund-sp" />
      <AppButton :disabled="submitting || Boolean(saving)" @click="close">Cancel</AppButton>
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
        :aria-describedby="runWhyId"
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
    :models="formChanges.models.length ? runModels : null"
    :suggested-name="card?.name || ''"
    :prompt="prompt"
    :negative="negative"
    :loras="recipeLoras"
    :overrides="recipeOverrides"
    :seed="seedMode === 'fixed' ? String(seed).trim() : ''"
    :settings-aside="seedMode === 'keep' ? KEEP_SEED_ASIDE : ''"
    :source-picture-id="pictureIds[0] ?? null"
    @close="saveOpen = false"
    @handoff="close"
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
 * editable. Every edit is an override `POST /workflows/run` applies to the
 * graph at submission time; neither Run nor closing writes one back.
 *
 * **A run of a workflow by itself can be kept** (#1850): where the form
 * differs from the workflow, a band above the footer counts what differs and
 * offers Save and Save as new…. One reading of the form (`formChanges`) is
 * what the band counts, what the run sends and what a save writes.
 *
 * **An edited field is marked in its label row**, never beside the control: the
 * four-column grid stays aligned only if a chip cannot change a cell's height.
 *
 * **Save as recipe reads Saved once a saved recipe already keeps this look**
 * (#1480), as the lightbox's Recipe tab has since F6 - without it the second
 * identical row was one press away from here.
 */
import {
  computed,
  nextTick,
  onBeforeUnmount,
  reactive,
  ref,
  useId,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";

import { characterThumbnailUrl } from "../../api/characters";
import { getPictureRecipe, getPixlstashNode } from "../../api/comfyui";
import { fetchWorkflowSets, listAdapters } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import { stackMemberIds } from "../../utils/stackMembers";
import { listSavedRecipes } from "../../api/recipes";
import {
  getLoraChain,
  getWorkflowCard,
  getWorkflowChanges,
  getWorkflowFormInputs,
  listWorkflowCards,
  openWorkflowRun,
  preflightWorkflowRun,
  readModelSwap,
  runWorkflowCard,
  saveWorkflow,
  saveWorkflowAsNew,
  setWorkflowInputs,
  setWorkflowPins,
  workflowCoverUrl,
} from "../../api/workflows";
import { useModelPickUp } from "../../composables/useModelPickUp";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useFilterStore } from "../../stores/useFilterStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { comfyuiLink } from "../../utils/comfyuiLink";
import { focusLater } from "../../utils/dom";
import { editLorasRoute, loraBase, loraStem } from "../../utils/loraChain";
import { fitPeople, fitWorkflows } from "../../utils/loraWorkflows";
import { deriveModelName } from "../../utils/modelShelf";
import { insertWords, namesWord, triggerWord, wordSpans } from "../../utils/triggerWords";
import { wouldDuplicate } from "../../utils/recipeKey";
import { runTaskLabel } from "../../utils/runTaskLabel";
import { UNMATCHED_REPLACEMENTS_TEXT } from "../../utils/workflowCard";
import {
  answering,
  changesWord,
  describeChanges,
  modelWord,
  noChanges,
  over,
  saveFailure,
  SIZE_INPUTS,
  suggestedName,
  whole,
} from "../../utils/workflowChanges";
import {
  choicesFor,
  nodesByAddress,
  optionsByAddress,
  parameterTitle,
  setEachRun,
} from "../../utils/workflowPins";
import {
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
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import Tooltip from "../widgets/Tooltip.vue";
import RunReasonNotice from "./RunReasonNotice.vue";
import RunResetChip from "./RunResetChip.vue";

const props = defineProps({
  /**
   * `{kind, pictureIds, workflowId, pickWorkflow, name, coverUrl}` - see
   * `useRunDialogStore`. Read once: a new source is a new popup, which App.vue
   * mounts fresh by keying it on `runOpened`.
   */
  source: { type: Object, default: null },
  /** `{set_id, project_id, character_id}` from the grid. */
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
const loadFailedId = useId();
const picsLabelId = useId();
const stackId = useId();
const stageId = useId();
/**
 * Bumped per read of a workflow into the form: the open, a move of the
 * picker, the read after a band save. Only the newest may write any of the
 * form. Two reads of ONE workflow can be out at once (picked, left, picked
 * again), and the key cannot tell those apart.
 *
 * Nothing guards one open against the next: `App.vue` mounts a new popup per
 * open, so `load()` runs once per instance. Calling it again on one instance
 * (a retry) would need a per-open guard back.
 */
let readAsk = 0;
const runDialog = useRunDialogStore();
const entityLists = useEntityListsStore();
const workflows = useWorkflowsStore();

const loading = ref(false);
const loadFailed = ref("");
const loadFailedLine = ref(null);
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
/** What ComfyUI offers for each of the card's parameters that is a choice. */
const formOptions = ref({});
/** The node each parameter is a setting of, for the ones the owner added. */
const formNodeNames = ref({});
/** The address "Set each run" is writing, and what went wrong if it failed. */
const freeing = ref("");
const fixedSummary = ref(null);
const formRoot = ref(null);
const freeError = ref("");
const recipe = ref(null);
const cards = ref([]);
const adapters = ref([]);
const reasons = ref([]);
/** A missing file downloaded after its folder's last scan is looked for (#1985). */
const { adding, pickUp } = useModelPickUp();
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
 * `{key, sha256, strength, person}`. Nothing the graph loads is replaced, so
 * they work on a workflow with no loader, or one whose loaders are all taken.
 * `person` is the id of the person whose LoRA the row holds, or null; those
 * rows come first, in the order picked (`settlePeople`).
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
/**
 * "Pictures are of": the people whose LoRA runs that the owner took off the
 * list, and the people they added to it, by id. Never remembered between
 * popups, unlike the set: a wrong person on a later run's pictures is worse
 * than one more pick. Nothing has to reset them: every opening is a new
 * instance (App.vue keys the popup on `runOpened`) and they are written
 * nowhere else.
 */
const ofRemoved = ref([]);
const ofAdded = ref([]);
const ofLabelId = useId();
/** People whose thumbnail would not load: they keep their row, with a glyph. */
const brokenFaces = reactive(new Set());

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
      ...loraEntry(row),
    })),
);

/**
 * The CLIP strength of a loader of the workflow's own chain, which knows it:
 * it follows the model strength where the two were one number, and is left as
 * the graph has it where they were set apart. The rule the Workflow tab
 * writes a strength by (`utils/workflowEdits.withLoraStrength`), so the two
 * editors save one loader the same way. A picture's slot names none.
 */
function clipStrength(row) {
  if (row.baseClip === undefined || !Number.isFinite(row.strength)) return {};
  return { strength_clip: row.clipTied ? row.strength : row.baseClip };
}

/**
 * What a row's loader is set to, as a run and a save both state it. The CLIP
 * strength is said with the model's: left unsaid, a save would write the one
 * and leave the graph's other beside it.
 */
function loraEntry(row) {
  return {
    sha256: row.sha256,
    strength_model: Number.isFinite(row.strength) ? row.strength : null,
    ...clipStrength(row),
  };
}

/**
 * The graph loaders taken out with their ×, as `skip_loras` takes them.
 *
 * Never also in `loras`: `changedLoras` passes over a removed row, so a loader
 * is either set or removed, and the route is never asked for both.
 */
const skippedLoras = computed(() =>
  loras.value
    .filter((row) => row.skipped)
    .map((row) => ({ node_id: row.node_id, field: row.field || "lora_name" })),
);

const dirty = computed(
  () =>
    // What the form changes, against what it opened with: changes waiting in
    // the Workflow tab are laid over the form, and they are not this popup's
    // work to lose.
    JSON.stringify(formChanges.value) !== changesAtOpen.value ||
    prompt.value !== basePrompt.value ||
    negative.value !== baseNegative.value ||
    Object.keys(picks).length > 0 ||
    loras.value.length !== initialLoraCount.value ||
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
    followLora(key);
    void switchCard(key, hadCard);
  },
});

/**
 * The owner picked another workflow. Only that: a save that moves the popup
 * onto the workflow it made goes by `keep`, which leaves the run's own
 * choices where they are, since that workflow is this graph.
 */
async function switchCard(key, keepEdits) {
  // What the last save and the server's verbs said is about the card left.
  leaveBand();
  // Addressed by node, which is the card being left's.
  choiceFixes.value = [];
  // A pick is addressed by a slot of the card it was made on; another card's
  // slots are other slots, so nothing carries over - and neither do the rows,
  // which are the set a pin would be written from.
  clearPicks();
  pictureInputs.value = [];
  inputsKey.value = "";
  try {
    if (await readWorkflow(key, keepEdits)) await runPreflight();
  } catch (err) {
    // Only a read that is still current rethrows: a workflow the picker has
    // since left is nobody's failure to be told.
    loadFailed.value = errorMessage(err, "Could not read that workflow.");
  }
}

/**
 * The workflow whose card, loaders and waiting changes the form holds, or "".
 *
 * From the instant the picker moves until those are read, the form is still a
 * reading of the workflow left (its edits, its LoRA rows, its version), and
 * nothing of it may be saved or run against the one arrived at. So the band
 * and Run both ask `formRead`, and a read that fails leaves it unset.
 */
const formKey = ref("");
const formRead = computed(() => formKey.value === activeKey.value);

/**
 * Read the workflow in the picker into the form: its card, and on a run of
 * the workflow by itself its loaders and what waits on it, laid over the
 * form. False when a newer read began meanwhile (the picker moved on, a save
 * read its workflow again), and then it has written nothing since: not its
 * card, not its failure.
 */
async function readWorkflow(key, keepEdits = false) {
  const ask = ++readAsk;
  // The picker's key is only written by a read (the open's own) or with a
  // read begun at once, so the newest read is of the workflow in the picker.
  const stale = () => ask !== readAsk;
  formKey.value = "";
  try {
    await loadCard(key, ask, { keepEdits });
    if (stale()) return false;
    if (runsWorkflowItself.value) {
      // The loaders, and what waits, are the workflow's: read for this one.
      await loadOwn(key, ask);
      if (stale()) return false;
      layOwn();
    }
  } catch (err) {
    // A read a newer one replaced has no failure to report either.
    if (stale()) return false;
    throw err;
  }
  formKey.value = key;
  return true;
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
/** Whether the last checkpoint set was typed in the row's box (`setCheckpoint`). */
const typedCheckpoint = ref(false);
/**
 * Whether the row holds a name the owner typed in its box, which a list
 * arriving under the caret would take away. A file picked from a list, or
 * laid from the Workflow tab (`layOwn`), is not that, and holds no list back.
 */
const checkpointTyped = computed(
  () => typedCheckpoint.value && checkpointEdit.value !== null,
);

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
 * What the LoRA section says beside its rows. A recipe's run with no loader
 * says where Add LoRA puts one. A run of the workflow by itself names the
 * LoRAs of its default recipe that have no row here (the base graph has no
 * loader holding them), which run as it stores them. A person's LoRA is
 * never one of them (the Person field says so).
 */
const ownLorasLine = computed(() => {
  if (hasRecipe.value) {
    return loraSlots.value.length
      ? ""
      : "This workflow has no LoRA loader. Add LoRA puts one in for this run.";
  }
  const rows = loraSlots.value.length > 0;
  const own = (card.value?.default_recipe?.loras || [])
    .filter((row) => !loaderOf(row, loraSlots.value))
    .map((row) => loraStem(row.filename || "") || row.filename)
    .filter(Boolean);
  if (own.length) {
    return rows
      ? `This workflow also loads ${own.join(", ")}, as it stores ${own.length === 1 ? "it" : "them"}.`
      : `This workflow's own LoRAs run as it stores them: ${own.join(", ")}.`;
  }
  return rows ? "" : "Add LoRA puts a LoRA in for this run; the workflow keeps its own.";
});
/**
 * The loaders of the workflow's own graph, for a run of it by itself
 * (`loadOwn`): `GET …/lora-chain`, the read the Workflow tab lists them from,
 * so both address one loader the same way.
 */
const chainSlots = ref([]);
/** The LoRA loaders the rows are made of: the picture's recipe's, or the workflow's. */
const loraSlots = computed(() =>
  recipe.value ? recipe.value.lora_slots || [] : chainSlots.value,
);
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
 * What the run's row in the Tasks tab is called (`runTaskLabel`), sent with
 * the run, or null to leave the naming to the server.
 *
 * A clone is one picture's recipe on its own workflow, nothing on the form
 * changed, run with THAT picture's seed. Typed as the fixed one, this form
 * knows. With "Keep the original's" only the server does: the seed it keeps
 * is this picture's, and it is a clone when the graph that runs was read from
 * this picture too. So that one is left to it.
 */
function taskLabel() {
  // `runsOwnWorkflow` is only ever true of ONE picture: no recipe is read
  // for several.
  const untouched = runsOwnWorkflow.value && !dirty.value;
  if (untouched && seedMode.value === "keep") return null;
  return runTaskLabel({
    clone:
      untouched &&
      seedMode.value === "fixed" &&
      Boolean(seedText.value) &&
      String(seed.value).trim() === seedText.value,
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
/**
 * Whether that picture is the workflow's own cover (a caller's `coverUrl` is
 * one too), rather than a picture this run is made from.
 */
const coverIsExample = computed(
  () =>
    Boolean(coverUrl.value) &&
    pictureIds.value[0] == null &&
    savedRecipe.value?.source_picture_id == null,
);
const seedText = computed(() => recipe.value?.seed_text || "");
/**
 * Whether this run would make the picture it is run from again: its own
 * recipe on its own workflow, nothing changed, its seed, and no model or
 * choice replaced for one this ComfyUI lacks. The library keeps no exact
 * duplicate, so such a run adds nothing, and the form says so first.
 */
const repeatsPicture = computed(() => {
  if (!runsOwnWorkflow.value || dirty.value || !seedText.value) return false;
  if (modelFixes.value.length || choiceFixes.value.length || checkpointFix.value) {
    return false;
  }
  return (
    seedMode.value === "keep" ||
    (seedMode.value === "fixed" && String(seed.value).trim() === seedText.value)
  );
});

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

/** Set the row, as the box does it: a pick or a laid change says `typed` is false. */
function setCheckpoint(raw, typed = true) {
  const text = String(raw ?? "").trim();
  checkpointEdit.value = text === checkpointBase.value ? null : text;
  typedCheckpoint.value = typed;
}

/**
 * The checkpoint as a run's `models` takes it, or [] when there is no row or
 * nothing in it. The row is always sent, edited or not, for the reason
 * `runBody` sends every value: the form is what runs.
 */
const checkpointRunModels = computed(() =>
  checkpointModel.value && checkpointValue.value && !checkpointFiles.value
    ? // Every loader of the one file: two loaders of it read as one row.
      checkpointModels.value.map((model) => ({
        address: model.address,
        filename: checkpointValue.value,
      }))
    : [],
);
/**
 * Every model this run names: the checkpoint row, with the other rows the
 * owner changed and the picks still waiting in the Workflow tab on top.
 */
const runModels = computed(
  () =>
    over({ models: checkpointRunModels.value }, { models: runChanges.value.models })
      .models,
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

/**
 * The picker's rows. For a checkpoint that is missing or replaced: that file
 * first, so the row shows what it was. Otherwise what the loader can load
 * (`modelChoices`), or none, and the row is a typed box.
 */
const checkpointOptions = computed(() => {
  const fix = checkpointFix.value;
  if (!fix?.options.length) {
    return withCurrent(
      modelChoices[CHECKPOINT_ROW] || [],
      checkpointBase.value,
      checkpointValue.value,
    );
  }
  const offered = [
    {
      value: fix.file,
      label: fix.missing
        ? `${shownModel(fix.file)} (not in this ComfyUI)`
        : `${shownModel(fix.file)} (in place of ${shownModel(fix.was)})`,
    },
    ...modelOptionsOf(fix.options),
  ];
  // A file laid from the Workflow tab, or brought from another workflow, may
  // be none of these, and a select whose value is not an option draws empty.
  const value = checkpointValue.value;
  return offered.some((option) => option.value === value)
    ? offered
    : [{ value, label: shownModel(value) }, ...offered];
});

const checkpointFixNote = computed(() => {
  const fix = checkpointFix.value;
  if (!fix) return "";
  const gone = fix.missing
    ? `${fix.file} is not in this ComfyUI.`
    : `${fix.was} is not in this ComfyUI, so this workflow loads ${fix.file} in its place (set in the Workflow tab).`;
  // Nothing to suggest until the replacements are read.
  if (fix.loading) return gone;
  if (fix.options.length) {
    return fix.narrowed
      ? `${gone} Pick another of the same base model.`
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
  setCheckpoint(filename, false);
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
async function offerCheckpoints(found, key) {
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
  const current = () => key === activeKey.value && ask === checkpointAsk;
  let answer;
  try {
    answer = await readSwap(key, graphFile, "checkpoint");
  } catch (err) {
    console.warn(`[run] could not read replacements for ${graphFile} on ${key}`, err);
    // Asked again on the next pre-flight rather than never.
    if (current()) checkpointFix.value = null;
    return;
  }
  if (!current()) return;
  // Typed over while the read was out: the owner's name stays in its box.
  if (checkpointTyped.value) {
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
 * The optional stages the base graph carries, as the rows draw them: the ones
 * the default recipe runs ticked, the ones it runs without not (`keep_stages`
 * switches one of those on for a run, #1849).
 */
const stageRows = computed(() =>
  (card.value?.specials || [])
    .filter((name) => name in STAGE_LABELS)
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

/** The stages of a set of changes switched *on* or off, as a run names them. */
function stagesSet(changes, on) {
  return Object.keys(changes.stages)
    .filter((name) => changes.stages[name] === on)
    .sort();
}

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
  // On or off: the word is the value, and any other word is not one. Sent as
  // text, ComfyUI would read "false" as on.
  if (typeof baseOf(field) === "boolean") {
    const word = String(raw ?? "").trim();
    return word === "true" ? true : word === "false" ? false : undefined;
  }
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
  // The whole list is the card's: mid-switch that is the workflow left's.
  if (!key || !formRead.value || freeing.value) return;
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
 * Create with LoRA… on a person: the trigger word of the LoRA their row runs,
 * or "" when it needs none.
 */
const fixedTrigger = computed(() => {
  if (loraSource.value?.entityType !== "character") return "";
  const id = Number(loraSource.value.entityId);
  const row = addedLoras.value.find((entry) => entry.person === id);
  return triggerWord(
    attachedLoras.value.find((lora) => row?.sha256 && lora.sha256 === row.sha256),
  );
});
/**
 * The prompt the box opens with, and what its reset chip goes back to.
 *
 * A caller's own `source.prompt` wins: the Recipes tab passes the prompt its
 * row shows, and the picture's re-read can come back without one. Create with
 * LoRA… on a person puts their trigger word first, unless it is already there,
 * so the box follows the workflow's own prompt with the word still in. Only
 * once the pre-flight says the graph takes a prompt: before that, or where it
 * failed, the word alone would be sent over a prompt nobody has read.
 */
const basePrompt = computed(() => {
  const own =
    props.source?.prompt ||
    recipe.value?.positive_prompt ||
    savedRecipe.value?.prompt ||
    graphPrompt.value?.positive_text ||
    "";
  const word = graphPrompt.value?.positive_settable ? fixedTrigger.value : "";
  return word && !namesWord(own, word) ? withTriggers([word], own) : own;
});
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
 * Create with LoRA's narrowing: against every LoRA attached to the person or
 * set while the first added row holds one of them, else against the LoRA that
 * row holds. See `utils/loraWorkflows.js`.
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
  const attached = attachedLoras.value.some((row) => row.sha256 === sha);
  const loras = attached
    ? attachedLoras.value
    : adapters.value.filter((row) => row.sha256 === sha).slice(0, 1);
  return { loras, ...fitWorkflows(cards.value, loras, handMadeSets.value) };
});

/** What the narrowing left out, said rather than hidden without a word. */
const loraFitNote = computed(() => {
  const fits = loraFits.value;
  if (!fits) return "";
  if (!attachedLoras.value.length) {
    return `No LoRA is attached to ${loraSource.value.name}. Assign one from the Models shelf, or add one below.`;
  }
  if (!fits.loras.length) {
    return "This LoRA is not on the Models shelf, so every workflow is listed.";
  }
  const known = fits.loras.filter((lora) => lora.base_model_family);
  if (!known.length) {
    return fits.loras.length > 1
      ? "These LoRAs have no base model recorded, so every workflow is listed."
      : "This LoRA has no base model recorded, so every workflow is listed.";
  }
  const parts = [];
  if (fits.clash.length) {
    parts.push(`${fits.clash.length} for another base model`);
  }
  if (fits.needsPicture.length) {
    parts.push(`${fits.needsPicture.length} that start from a picture`);
  }
  if (!fits.match.length && !fits.unknown.length) {
    const models = [...new Set(known.map((lora) => lora.base_model).filter(Boolean))];
    return `None of your workflows is for ${models.join(" or ") || "this base model"}.`;
  }
  return parts.length ? `Not listed: ${parts.join(", ")}.` : "";
});

/**
 * Create with LoRA: the attached LoRA the run adds follows the workflow. A
 * person with a LoRA for each of two base models is offered both models'
 * workflows, and picking one must load the LoRA made for it, so a row holding
 * an attached LoRA that does not match *key*'s checkpoint takes the first that
 * does and no other row holds. A row that already matches, or a workflow
 * nothing free matches, is left be.
 */
function followLora(key) {
  const row = addedLoras.value[0];
  const entry = loraFits.value?.match.find((fit) => fit.card.id === key);
  if (!row || !entry || entry.loras.some((lora) => lora.sha256 === row.sha256)) return;
  if (!attachedLoras.value.some((lora) => lora.sha256 === row.sha256)) return;
  // Never a file another row already holds: one loader asked for twice.
  const held = new Set(addedLoras.value.map((other) => other.sha256));
  const free = entry.loras.find((lora) => !held.has(lora.sha256));
  if (free) row.sha256 = free.sha256;
}

const workflowOptions = computed(() => {
  const fits = loraFits.value;
  if (fits) {
    // Matches first, then the workflows whose base model nobody has read,
    // said so: they may fit, and hiding them would hide every workflow on a
    // model the shelf has not identified.
    const known = fits.loras.some((lora) => lora.base_model_family);
    return [
      ...fits.match.map((entry) => ({ value: entry.card.id, label: entry.card.name })),
      ...fits.unknown.map((entry) => ({
        value: entry.card.id,
        label:
          known && !entry.checkpoint?.base_model_family
            ? `${entry.card.name} (base model not known)`
            : entry.card.name,
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

/**
 * The LoRAs a row may be given. A LoRA this library hides is not offered;
 * `adapters` still holds it, so a workflow that already loads one names it
 * (`optionsFor`).
 */
const adapterOptions = computed(() =>
  [
    ...adapters.value,
    // An attached LoRA the shelf classes `unknown` is not in `adapters`,
    // which reads `file_kind=adapter` only.
    ...attachedLoras.value.filter(
      (row) => !adapters.value.some((adapter) => adapter.sha256 === row.sha256),
    ),
  ]
    .filter((adapter) => adapter?.sha256 && !adapter.hidden)
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
    // Create with LoRA… on a person takes others beside them; on a picture
    // set it is a run of the set's look, and offers nobody.
    loraSource.value?.entityType !== "set",
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
        // A person is offered for a LoRA of theirs, and a hidden one is not
        // offered, so it vouches for nobody here.
        [...adapters.value, ...unknownLoras.value].filter((row) => !row.hidden),
        entityLists.characters,
      )
    : { people: [], clash: 0, unknown: 0, family: null, baseModel: null },
);
/** The people picked for this run, in the order picked; never the fixed one. */
const personIds = ref([]);
/** Everyone the run is of: the person it was opened on, then the picks. */
const chosenPersonIds = computed(() => [
  ...(fixedPerson.value ? [fixedPerson.value.id] : []),
  ...personIds.value,
]);
const personTiles = computed(() => {
  const fixed = fixedPerson.value;
  const offered = personFits.value.people.filter(
    (person) => person.id !== fixed?.id,
  );
  return (fixed ? [fixed, ...offered] : offered).map((person) => ({
    ...person,
    trigger: personWord(person.id),
  }));
});
/** Absent, not empty, in a library where nobody has a LoRA. */
const showPerson = computed(
  () =>
    Boolean(fixedPerson.value) ||
    personFits.value.people.length > 0 ||
    personFits.value.clash + personFits.value.unknown > 0,
);
const personNote = computed(() => {
  if (fixedPerson.value) return "";
  const { people, clash, unknown, family, baseModel } = personFits.value;
  if (!family && baseModel) {
    return `PixlStash cannot match LoRAs to ${baseModel}.`;
  }
  if (!family) {
    return "This workflow's base model is not known, so no person's LoRA can be matched to it.";
  }
  if (!people.length) return "PixlStash has no LoRAs for this model.";
  const parts = [];
  if (clash) parts.push(`${clash} whose LoRA is for another base model`);
  if (unknown) parts.push(`${unknown} whose LoRA has no base model recorded`);
  // The tiles say who can be picked; only who was left out needs words.
  return parts.length ? `Not listed: ${parts.join(", ")}.` : "";
});

function personName(id) {
  return personTiles.value.find((person) => person.id === id)?.name || "this person";
}

/**
 * The trigger word a person's run carries: their row's LoRA's, or for one not
 * picked, the LoRA a pick would add, as `settlePeople` chooses it (their first
 * that fits and no other person's row holds). "" for none.
 */
function personWord(id) {
  const shelf = [...attachedLoras.value, ...adapters.value, ...unknownLoras.value];
  const row = addedLoras.value.find((entry) => entry.person === id && entry.sha256);
  const held = new Set(
    addedLoras.value
      .filter((entry) => entry.person != null && entry.person !== id)
      .map((entry) => entry.sha256),
  );
  const sha256 =
    row?.sha256 ??
    personFits.value.people
      .find((person) => person.id === id)
      ?.loras.find((lora) => !held.has(lora.sha256))?.sha256;
  return sha256 ? triggerWord(shelf.find((entry) => entry.sha256 === sha256)) : "";
}

/** What an added row is called, by whose it is or by where it stands. */
function addedRowName(row, index) {
  return row.person == null
    ? `added LoRA ${index + 1}`
    : `${personName(row.person)}'s LoRA`;
}

/**
 * The LoRAs a person's row offers: theirs that fit, never the whole shelf,
 * and never the file another person's row already holds, which would be one
 * loader asked for twice.
 */
function personLoraOptions(row) {
  const own =
    row.person === fixedPerson.value?.id
      ? attachedLoras.value
      : personFits.value.people.find((person) => person.id === row.person)
          ?.loras || [];
  const taken = new Set(
    addedLoras.value
      .filter((other) => other.person != null && other.key !== row.key)
      .map((other) => other.sha256),
  );
  return own.filter((lora) => !taken.has(lora.sha256)).map(adapterOption);
}

/**
 * The trigger word of each picked person's LoRA, in the order picked:
 * `[{word, person}]`, the person by name. Read off each row's own pick, so
 * changing which of a person's LoRAs runs changes the word; a LoRA that needs
 * none adds nothing, and a word two people share is listed once.
 */
const personTriggers = computed(() => {
  const shelf = [...attachedLoras.value, ...adapters.value, ...unknownLoras.value];
  const triggers = [];
  for (const row of addedLoras.value) {
    if (row.person == null || !row.sha256) continue;
    const word = triggerWord(shelf.find((entry) => entry.sha256 === row.sha256));
    const known = triggers.some(
      (trigger) => trigger.word.toLowerCase() === word.toLowerCase(),
    );
    if (word && !known) triggers.push({ word, person: personName(row.person) });
  }
  return triggers;
});
/** How many missing words get a button each; past it, one adds them all. */
const MAX_TRIGGER_BUTTONS = 3;
/**
 * The buttons of the trigger-word line, `[{label, hint, words}]`: one for each
 * word the prompt (which can be set here) lacks. Empty is no line at all.
 */
const triggerAdds = computed(() => {
  if (promptUnset.value) return [];
  const missing = personTriggers.value.filter(
    (trigger) => !namesWord(prompt.value, trigger.word),
  );
  if (missing.length > MAX_TRIGGER_BUTTONS) {
    const words = missing.map((trigger) => trigger.word);
    return [
      {
        label: `Add all ${words.length}`,
        hint: `Add all ${words.length} missing trigger words: ${words.join(", ")}`,
        words,
      },
    ];
  }
  return missing.map(({ word, person }) => ({
    label: `Add ${word}`,
    hint: `Add ${word}, ${person}'s trigger word`,
    words: [word],
  }));
});
/** The trigger-word line for a reader: the words still missing, or nothing. */
const triggerLive = computed(() => {
  const words = triggerAdds.value.flatMap((add) => add.words);
  return words.length ? `Trigger word missing: ${words.join(", ")}.` : "";
});
const promptField = ref(null);

/** *text* with *words* put first, where a trigger word goes. */
function withTriggers(words, text) {
  return [...words, text.trim()].filter(Boolean).join(", ");
}

/**
 * Put the words where the caret was in the box (first, where a trigger word
 * goes, when it was never placed) and hand the box back with the caret after
 * them.
 */
async function addTriggers(words) {
  const at = promptField.value?.selection?.();
  const next = insertWords(prompt.value, words, at?.start, at?.end);
  prompt.value = next.text;
  // The box must hold the new text before the caret can be put in it.
  await nextTick();
  // The button goes with the suggestion; focus must not fall to the page.
  promptField.value?.focus?.(next.caret);
}

/** People unpicked by hand, whom the prompt's text does not pick again. */
const unpickedIds = new Set();

/**
 * The prompt as the owner types it. A person's name, once typed past, becomes
 * their trigger word where that is another word: not where the word holds the
 * name ("Mira" of "Mira Vale" is the word being typed), nor where the prompt
 * already names it (the name beside it is then meant). A person whose trigger
 * word is typed past is picked. "Typed past" is a hit the caret is not at the end
 * of and the text goes on after, so "Mira" on the way to "Miranda" counts for
 * nothing. Never unpicks: an edit must not take a LoRA off, and a person
 * unpicked by hand is neither picked again by the text nor has their name
 * swapped.
 */
function typePrompt(text) {
  const live = promptField.value?.selection?.();
  let caret = live ? live.start : text.length;
  let next = text;
  const past = (span) => span.end !== caret && span.end < next.length;
  const terms = personTiles.value
    .flatMap((person) => [person.name, person.trigger])
    .map((term) => String(term || "").trim())
    .filter(Boolean);
  // A hit of *term* that is its own, not part of a longer name or word typed
  // round it: "Mira" in "Mira Vale", when Mira Vale is somebody too.
  const own = (span, term) =>
    !terms.some(
      (other) =>
        other.length > term.length &&
        wordSpans(next, other).some((hit) => hit.start <= span.start && hit.end >= span.end),
    );
  for (const person of personTiles.value) {
    const word = person.trigger;
    if (!word) continue;
    const name = String(person.name || "").trim();
    let swapped = false;
    // Not for a person unpicked by hand: their word would run without their LoRA.
    if (
      name &&
      !unpickedIds.has(person.id) &&
      !namesWord(word, name) &&
      !namesWord(next, word)
    ) {
      const span = wordSpans(next, name).find((hit) => past(hit) && own(hit, name));
      if (span) {
        next = next.slice(0, span.start) + word + next.slice(span.end);
        if (caret >= span.end) caret += word.length - (span.end - span.start);
        // Edited inside the name: the caret goes after the word, not into it.
        else if (caret > span.start) caret = span.start + word.length;
        swapped = true;
      }
    }
    if (
      !chosenPersonIds.value.includes(person.id) &&
      !unpickedIds.has(person.id) &&
      // A swapped name was typed past, wherever the caret now sits after it.
      (swapped || wordSpans(next, word).some((hit) => past(hit) && own(hit, word)))
    ) {
      pickPerson(person.id);
    }
  }
  prompt.value = next;
  // The box shows the swapped name only once it redraws; the caret goes back
  // where the owner was typing.
  if (next !== text && live) nextTick(() => promptField.value?.focus?.(caret));
}

/**
 * Pick a person for this run, or unpick one already picked. Null is "No one":
 * every pick comes off. Their rows follow in `settlePeople`.
 */
function pickPerson(id) {
  if (id == null) {
    personIds.value.forEach((picked) => unpickedIds.add(picked));
    personIds.value = [];
  } else if (personIds.value.includes(id)) {
    unpickedIds.add(id);
    personIds.value = personIds.value.filter((picked) => picked !== id);
  } else if (id !== fixedPerson.value?.id) {
    unpickedIds.delete(id);
    personIds.value = [...personIds.value, id];
  }
  settlePeople();
}

/**
 * Make the added rows say what the picks say: one row per picked person,
 * first among the added rows and in the order picked, each holding one of
 * that person's LoRAs for this workflow.
 *
 * A pick the workflow no longer offers comes off rather than being sent, and
 * a row whose LoRA is no longer that person's takes their first one. No two
 * people's rows hold one file: a person whose every LoRA is already on
 * somebody else's row stays picked with no row of their own (that loader IS
 * theirs too), and gets one the moment the file is free.
 */
function settlePeople() {
  const offered = new Map(
    personFits.value.people.map((person) => [person.id, person]),
  );
  const fixedId = fixedPerson.value?.id ?? null;
  const picked = personIds.value.filter(
    (id) => id !== fixedId && offered.has(id),
  );
  if (picked.length !== personIds.value.length) personIds.value = picked;
  const rows = addedLoras.value;
  const fixedRows =
    fixedId == null ? [] : rows.filter((row) => row.person === fixedId);
  const held = new Set(fixedRows.map((row) => row.sha256));
  const peopleRows = [];
  for (const id of picked) {
    const free = offered.get(id).loras.filter((lora) => !held.has(lora.sha256));
    const row =
      rows.find(
        (entry) =>
          entry.person === id &&
          free.some((lora) => lora.sha256 === entry.sha256),
      ) || (free.length ? addedRow(free[0].sha256, 1, id) : null);
    if (!row) continue;
    held.add(row.sha256);
    peopleRows.push(row);
  }
  const next = [
    ...fixedRows,
    ...peopleRows,
    ...rows.filter((row) => row.person == null),
  ];
  // Only a real change is written: this runs from a watcher on the rows.
  if (next.length !== rows.length || next.some((row, at) => row !== rows[at])) {
    addedLoras.value = next;
  }
}

// Another workflow may be for another base model, the shelf may attach
// another LoRA, and a row's own select may free a file somebody else's pick
// was waiting for.
watch(
  [personFits, () => addedLoras.value.map((row) => row.sha256).join()],
  settlePeople,
);

const setOptions = computed(() => [
  { value: "", label: "No set" },
  ...entityLists.pictureSets.map((row) => ({
    value: String(row.id),
    label: row.name,
  })),
]);

/**
 * The people whose LoRA this run loads, `[{id, name}]` in id order, read off
 * the form's rows the way the server's `_lora_people` reads the graph. A row
 * taken off or at strength zero loads nobody.
 *
 * Read off the form rather than the pre-flight, so the list moves as the form
 * does; the server takes the list it is sent (`character_ids`), so a loader no
 * output reads, which counts here and not on the server, still links whom
 * the side column says.
 */
const loraPeople = computed(() => {
  const owners = new Map();
  for (const lora of [...adapters.value, ...unknownLoras.value]) {
    for (const entry of lora.attachments || []) {
      if (entry.entity_type !== "character" || !lora.sha256) continue;
      const digest = lora.sha256.toLowerCase();
      owners.set(digest, [...(owners.get(digest) || []), Number(entry.entity_id)]);
    }
  }
  const ids = new Set();
  for (const row of [...loras.value, ...addedLoras.value]) {
    if (row.skipped || !row.sha256 || row.strength === 0) continue;
    // A picked person's row is theirs whatever the shelf read answered.
    if (row.person != null) ids.add(row.person);
    for (const id of owners.get(row.sha256.toLowerCase()) || []) ids.add(id);
  }
  return [...ids].sort((a, b) => a - b).map((id) => ({ id, name: ofName(id) }));
});

function ofName(id) {
  const row = entityLists.characters.find((item) => Number(item.id) === id);
  return row?.name || personName(id);
}

/**
 * "Pictures are of", row by row: the LoRA people first (struck through,
 * `off`, where taken off), then the people added by hand.
 */
const ofRows = computed(() => {
  const byLora = loraPeople.value.map((person) => ({
    ...person,
    byLora: true,
    off: ofRemoved.value.includes(person.id),
  }));
  const listed = new Set(byLora.map((row) => row.id));
  return [
    ...byLora,
    ...ofAdded.value
      .filter((id) => !listed.has(id))
      .map((id) => ({ id, name: ofName(id), byLora: false, off: false })),
  ];
});
/** Who the run's pictures are linked to: every row not struck through. */
const ofPeopleIds = computed(() =>
  ofRows.value.filter((row) => !row.off).map((row) => row.id),
);

/** "Add someone": everybody in the library not on the list already. */
const addOptions = computed(() => {
  const listed = new Set(ofRows.value.map((row) => row.id));
  return [
    { value: "", label: "Add someone" },
    ...entityLists.characters
      .filter((row) => !listed.has(Number(row.id)))
      .map((row) => ({ value: String(row.id), label: row.name })),
  ];
});

function addPerson(value) {
  if (!value) return;
  const id = Number(value);
  if (!ofAdded.value.includes(id)) ofAdded.value = [...ofAdded.value, id];
}

/**
 * Take a person off the list. One whose LoRA runs stays, struck through,
 * with Undo, so the gap shows; their LoRA is left alone. One added by hand
 * just goes, back into "Add someone".
 */
function removePerson(row) {
  ofAdded.value = ofAdded.value.filter((id) => id !== row.id);
  if (row.byLora) {
    ofRemoved.value = [...ofRemoved.value, row.id];
    focusLater(`[data-of="${row.id}"] [data-focus="undo"]`);
  } else {
    focusLater(".rund-of-add .app-select__field");
  }
}

function restorePerson(row) {
  ofRemoved.value = ofRemoved.value.filter((id) => id !== row.id);
  focusLater(`[data-of="${row.id}"] [data-focus="remove"]`);
}

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
/**
 * Why Run waits on the popup's own work, or "": the workflow in the picker
 * not read into the form yet, or a band save still out. Between a save's
 * POST and the re-read behind it the form is neither the workflow as it was
 * nor as it is, and a run then would send the one over the other.
 */
const runWait = computed(() => {
  // A read that failed is said where it failed, and is no wait.
  if (!activeKey.value || loadFailed.value) return "";
  if (!formRead.value) return "Reading this workflow…";
  return saving.value ? "Run waits for the save to finish." : "";
});
const canRun = computed(
  () => formRead.value && !saving.value && !runBlocker.value && !submitting.value,
);
/** The sentence that says why Run does nothing, wherever it is printed. */
const runWhyId = computed(() => {
  if (loadFailed.value) return loadFailedId;
  return runWait.value || runBlocker.value ? blockerId : undefined;
});
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

// ── The other models (#1850) ──────────────────────────────────────────────

/** The key the checkpoint row's choices are kept under in `modelChoices`. */
const CHECKPOINT_ROW = "checkpoint";
/** The kinds `model-swap?replacing=` can answer for, and what a row is called. */
const MODEL_ROW_LABELS = { vae: "VAE", text_encoder: "Text encoder" };

/**
 * The models beside the checkpoint that the default recipe loads, one row per
 * file: two loaders of one file read as one row, as the checkpoint's do. Only
 * on a run of the workflow by itself, where a change can be kept.
 */
const modelRows = computed(() => {
  if (!runsWorkflowItself.value) return [];
  const rows = new Map();
  for (const model of card.value?.default_recipe?.models || []) {
    // A model whose name was forgotten has no file to go back to.
    if (!model.address || model.kind === "checkpoint" || !model.filename) continue;
    const key = `${model.kind}\n${model.filename}`;
    const row = rows.get(key) || {
      key,
      kind: model.kind,
      file: model.filename,
      addresses: [],
    };
    row.addresses.push(model.address);
    rows.set(key, row);
  }
  const list = [...rows.values()];
  return list.map((row) => {
    const word = MODEL_ROW_LABELS[row.kind] || modelWord(row.addresses[0]).one;
    const alike = list.filter((other) => other.kind === row.kind);
    return {
      ...row,
      label: alike.length > 1 ? `${word} ${alike.indexOf(row) + 1}` : word,
    };
  });
});
/** row key -> the owner's file, over the default recipe's. */
const modelEdits = reactive({});
/** row key -> `[{value, label}]`: what the server says that loader can load. */
const modelChoices = reactive({});

/** A model file as a person reads it: never its extension or folder. */
function shownModel(file) {
  return deriveModelName(file) || loraStem(file) || String(file || "");
}

/**
 * Shelf models as a picker's rows: each by the name a person gave it, else
 * by its file read as a name. Two quantisations of one model read alike, so
 * those are told apart by their file.
 */
function modelOptionsOf(models) {
  const names = models.map(
    (model) => model.display_name || shownModel(model.filename),
  );
  return models.map((model, at) => ({
    value: model.filename,
    label:
      names.indexOf(names[at]) === names.lastIndexOf(names[at])
        ? names[at]
        : loraStem(model.filename),
  }));
}

/**
 * *choices* with the row's own two files among them, first: the one it has
 * now and the workflow's. A select whose value is not an option draws empty.
 */
function withCurrent(choices, base, value) {
  if (!choices.length) return [];
  // The shelf may spell the workflow's own file with its folders: that
  // choice IS the row as saved, so it takes the workflow's spelling.
  const listed = choices.map((choice) =>
    loraBase(choice.value) === loraBase(base) ? { ...choice, value: base } : choice,
  );
  const own = [...new Set([value, base].filter(Boolean))]
    .filter((file) => !listed.some((choice) => choice.value === file))
    .map((file) => ({ value: file, label: shownModel(file) }));
  return [...own, ...listed];
}

function modelValue(row) {
  return modelEdits[row.key] ?? row.file;
}

function modelOptions(row) {
  return withCurrent(modelChoices[row.key] || [], row.file, modelValue(row));
}

function pickModel(row, file) {
  if (!file || loraBase(file) === loraBase(row.file)) delete modelEdits[row.key];
  else modelEdits[row.key] = file;
  void runPreflight();
}

/**
 * `model-swap?replacing=` for one loader's file, asked once: the row's choices
 * and the offer for a missing checkpoint are the same read, and the server
 * goes over the whole shelf to answer it. A read that failed is asked again.
 */
const swapReads = new Map();
function readSwap(key, file, kind) {
  const id = `${key}\n${kind}\n${file}`;
  if (!swapReads.has(id)) {
    const read = Promise.resolve(
      readModelSwap(key, { replacing: file, slotKind: kind }),
    );
    swapReads.set(id, read);
    read.catch(() => swapReads.delete(id));
  }
  return swapReads.get(id);
}

/**
 * Read what each model row may be set to, beside the popup and never ahead of
 * it: `model-swap?replacing=`, the Workflow tab's own "Replace with…" list
 * (what goes with the checkpoint and the loader can load). A row nothing
 * answers for stays as it was: text, or the checkpoint's typed box.
 */
function loadModelChoices(key, read) {
  for (const row of Object.keys(modelChoices)) delete modelChoices[row];
  if (!runsWorkflowItself.value || !key) return;
  const asks = modelRows.value
    .filter((row) => row.kind in MODEL_ROW_LABELS)
    .map((row) => ({ row: row.key, kind: row.kind, file: row.file }));
  if (checkpointModel.value?.filename && !checkpointFiles.value) {
    asks.push({
      row: CHECKPOINT_ROW,
      kind: "checkpoint",
      file: inPlaceOf(checkpointModel.value.filename),
    });
  }
  // ponytail: one read per model row per open, each a pass over the shelf.
  // One route answering for every loader at once is the upgrade if it shows.
  for (const ask of asks) {
    readSwap(key, ask.file, ask.kind)
      .then((answer) => {
        if (read !== readAsk) return;
        // Typed over while the read was out: the owner's name stays in its
        // box, which a list arriving under the caret would take away.
        if (ask.row === CHECKPOINT_ROW && checkpointTyped.value) return;
        const options = modelOptionsOf(answer?.replacements || []);
        if (options.length) modelChoices[ask.row] = options;
      })
      .catch((err) => {
        console.warn(`[run] could not read what may replace ${ask.file} on ${key}`, err);
      });
  }
}

// ── Save what you ran (#1850) ─────────────────────────────────────────────

/**
 * Whether this popup runs a workflow by itself. Only that run's edits are
 * changes TO the workflow: a run from pictures or a saved recipe is a recipe
 * laid over it (rule 8 of #1846), and gets no band.
 */
const runsWorkflowItself = computed(
  () => kind.value === "card" && !pictureIds.value.length && !savedRecipe.value,
);
/**
 * `GET …/changes` for the workflow in the picker, or null: its `version`,
 * which a save names, and what waits on it in the Workflow tab.
 */
const changesState = ref(null);
/** The part of what waits that the form took as edits of its own (`layOwn`). */
const laid = ref(noChanges());
/** The part it has no control for, which a run of the workflow still carries. */
const carried = ref(noChanges());
/**
 * All of what waited as `layOwn` read it, `laid` and `carried` together, or
 * null where it could not be read: a run's `waited`. Kept by `layOwn` and not
 * read off `changesState`, which a read that is out has already emptied.
 */
const seen = ref(null);
/** `formChanges` as the form opened, for `dirty`. */
const changesAtOpen = ref(JSON.stringify(noChanges()));
const savesOffered = computed(
  () => runsWorkflowItself.value && formRead.value && changesState.value !== null,
);

/** The parameters the owner changed; both halves of a Size when either moved. */
const changedValues = computed(() => {
  const size = sizeEdited.value ? new Set(sizeFields.value.map(address)) : new Set();
  return defaults.value
    .filter((field) => isEdited(field) || size.has(address(field)))
    .map((field) => ({
      slot_label: field.slot_label,
      input_name: field.input_name,
      value: runValue(field),
    }))
    .filter((row) => row.value !== null && row.value !== undefined);
});

/**
 * **What this form changes against the workflow**, as a `WorkflowChanges`.
 *
 * The one reading of it: the band counts and names this, the run sends it and
 * a save writes it, so a save can never keep something the run did not use.
 * It holds what carries a ↺ chip and nothing else: prompt, seed, count, the
 * pictures put in, the people picked and a LoRA added by hand are the run's,
 * and have no field here.
 */
const formChanges = computed(() => ({
  models: [
    ...(checkpointEdited.value ? checkpointRunModels.value : []),
    ...modelRows.value.flatMap((row) =>
      row.key in modelEdits
        ? row.addresses.map((at) => ({ address: at, filename: modelEdits[row.key] }))
        : [],
    ),
  ],
  loras: changedLoras.value,
  skip_loras: skippedLoras.value,
  stages: { ...stageChoice },
  values: changedValues.value,
}));
/** With what still waits in the Workflow tab under it: what a run carries. */
const runChanges = computed(() => over(carried.value, formChanges.value));

/** What the form says a thing is now, for `answering`. */
const formAnswers = {
  model(at) {
    if (checkpointRunModels.value.some((model) => model.address === at)) {
      return checkpointValue.value;
    }
    const row = modelRows.value.find((entry) => entry.addresses.includes(at));
    return row ? modelValue(row) : undefined;
  },
  lora(nodeId, field) {
    const row = loraRowAt(nodeId, field);
    if (!row) return undefined;
    // `null` only for a row `skippedLoras` also names, which is what
    // `answering` relies on. Otherwise as a changed row states it.
    return row.skipped ? null : loraEntry(row);
  },
  stage(name) {
    return stageRows.value.some((row) => row.name === name) ? stageOn(name) : undefined;
  },
  value(slotLabel, inputName) {
    const field = fieldAt(slotLabel, inputName);
    return field ? (runValue(field) ?? undefined) : undefined;
  },
};
/**
 * What a save sends: `formChanges`, with the form's present answer stated for
 * every waiting change it took and the owner then undid (`answering`).
 */
const saveChanges = computed(() =>
  answering(formChanges.value, laid.value, formAnswers),
);

function fieldAt(slotLabel, inputName) {
  return defaults.value.find(
    (field) => field.slot_label === slotLabel && field.input_name === inputName,
  );
}

function loraRowAt(nodeId, field) {
  return loras.value.find(
    (row) => row.node_id === String(nodeId) && row.field === (field || "lora_name"),
  );
}

/** How many things differ, and which: the band's two lines. */
const band = computed(() =>
  savesOffered.value
    ? describeChanges(runChanges.value, {
        value: (slotLabel, inputName) => fieldAt(slotLabel, inputName)?.label,
      })
    : { count: 0, text: "" },
);
/** The band's last line: what a save never keeps. */
const bandStays = computed(() => {
  const byHand = addedLoras.value.filter((row) => row.person == null && row.sha256);
  return byHand.length
    ? `Prompt, seed, count, the people picked and the ${byHand.length === 1 ? "LoRA" : "LoRAs"} added here stay with the run.`
    : "Prompt, seed, count and the people picked stay with the run.";
});

/** The last dry run's `{save, save_as_new}`, each `{open, reason}`, or null. */
const verbs = ref(null);
/** The server's sentence for a closed Save, printed as sent; "" when it is open. */
const saveClosed = computed(() =>
  verbs.value?.save && !verbs.value.save.open
    ? verbs.value.save.reason || "Save is not open for these changes."
    : "",
);
const saveReasonId = useId();
/** "save" or "new" while that write is out, else "". */
const saving = ref("");
const bandError = ref("");
/** What the last save here did, shown where the band was. */
const bandNote = ref("");
const bandLive = ref("");
const naming = ref(false);
const newName = ref("");
const nameField = ref(null);
const asNewButton = ref(null);
const bandNoteLine = ref(null);
const bandBox = ref(null);
/** The verb a save is sent with: Save, or the name question's own button. */
const bandVerb = ref(null);
const runWaitLine = ref(null);

/** Asked a beat after the last edit, never per keystroke. */
const VERBS_ASK_MS = 300;
let verbAsk = 0;
let verbTimer = null;

// Which verbs are open is the server's to say (the checkpoint rule, a
// workflow changed since): asked as a dry run of the very save, whenever the
// set changes. Only the newest question's answer is kept.
watch(
  () => (band.value.count ? JSON.stringify(saveChanges.value) : ""),
  (asked) => {
    clearTimeout(verbTimer);
    const ask = ++verbAsk;
    bandError.value = "";
    if (!asked) {
      verbs.value = null;
      naming.value = false;
      return;
    }
    const key = activeKey.value;
    verbTimer = setTimeout(async () => {
      try {
        const answer = await saveWorkflow(key, {
          changes: JSON.parse(asked),
          version: changesState.value?.version ?? null,
          dry_run: true,
        });
        if (ask !== verbAsk || key !== activeKey.value) return;
        verbs.value = { save: answer?.save, save_as_new: answer?.save_as_new };
      } catch (err) {
        // Not knowing is not a refusal: the buttons stay, and the save itself
        // answers with its reason.
        if (ask === verbAsk) verbs.value = null;
        console.warn(`[run] could not ask which saves are open for ${key}`, err);
      }
    }, VERBS_ASK_MS);
  },
);
onBeforeUnmount(() => clearTimeout(verbTimer));

/** Whether the band is on screen: something differs, on a form that is drawn. */
const bandShown = computed(
  () => band.value.count > 0 && !loading.value && !loadFailed.value,
);

// The band's arrival is said once; its count and list are there to be read.
// Unsaid when it goes: the same words written again are no change to say.
watch(
  bandShown,
  (shown) => {
    if (shown) bandNote.value = "";
    bandLive.value = shown
      ? "This run differs from the workflow. Save and Save as new are above the Run button."
      : "";
  },
);

/** Forget what the band said about the workflow being left. */
function leaveBand() {
  clearTimeout(verbTimer);
  verbAsk += 1;
  verbs.value = null;
  bandError.value = "";
  bandNote.value = "";
  naming.value = false;
}

/**
 * Every edit the band counts, taken off the form. The run's own stay. The
 * LoRA rows are not put back here: every caller reads the workflow again, and
 * `layOwn` builds them anew from the chain the save left.
 */
function clearChanges() {
  for (const key of Object.keys(edits)) delete edits[key];
  for (const key of Object.keys(editedLabels)) delete editedLabels[key];
  for (const key of Object.keys(drafts)) delete drafts[key];
  for (const key of Object.keys(modelEdits)) delete modelEdits[key];
  clearStages();
  checkpointEdit.value = null;
}

/** A save landed on *key*: the Workflow tab and the grid show the workflow as it was. */
function tellSaved(key, savedAs = "") {
  runDialog.changesSaved(key, savedAs);
  workflows.invalidate();
}

/**
 * Save: what the band names, written over the workflow as its next version.
 * The workflow's values are then what the form shows, so its baseline is read
 * again and the chips go; prompt, seed, count, people and pictures stay.
 */
async function saveOver() {
  if (saving.value || submitting.value || !band.value.count || saveClosed.value) return;
  await keep("save", async (key) => {
    await saveWorkflow(key, {
      changes: saveChanges.value,
      version: changesState.value?.version ?? null,
    });
    return { key, done: "Saved to this workflow." };
  });
}

/**
 * One band save, either verb. *write* is the POST: it answers the workflow
 * the form is of afterwards, and the line that says what was done.
 *
 * **The save is settled when its POST answers**, and only that failing is
 * printed as a save that failed. What follows is reading the workflow again,
 * so the chips go and the LoRA rows are built from the chain the save left;
 * prompt, seed, count, people and pictures are the run's and are not touched,
 * for a new workflow as for this one, since the new one is this graph. A
 * read that fails there is said as that. The form is then a reading of a
 * workflow that has moved on, so it is taken down (`loadFailed`): nothing of
 * it can be run over the save, and no second workflow made from it.
 *
 * **Focus is never left on nothing by a save.** A keyboard in the band stays
 * where it is. Focus anywhere else waits on the footer's sentence: a click
 * does not focus a button everywhere (Safari), so a verb can be pressed with
 * the caret still in the form, which goes inert. Not on the verb, which is
 * disabled while it spins, nor on the band, which leaves when the POST lands;
 * whatever it held waits on that sentence from then. The save's end puts it
 * on the line saying what was done, or back on the verb where it was refused.
 */
async function keep(verb, write) {
  const waited = !bandBox.value?.contains(document.activeElement);
  saving.value = verb;
  bandError.value = "";
  // Once drawn: the sentence comes with `saving`.
  const waitInFooter = () => void nextTick(() => runWaitLine.value?.focus());
  if (waited) waitInFooter();
  const from = activeKey.value;
  const here = (key) => key === activeKey.value;
  try {
    let saved = null;
    try {
      saved = await write(from);
    } catch (err) {
      if (here(from)) bandError.value = saveFailure(err);
    }
    if (saved) tellSaved(from, saved.key === from ? "" : saved.key);
    if (!saved?.key || !here(from)) {
      // The band is still up. Focus that waited goes to the verb pressed,
      // once that can hold it again; one that had the keyboard gets it back
      // from `AppButton` itself, and the name field never lost it.
      saving.value = "";
      if (waited) {
        await nextTick();
        bandVerb.value?.focus?.();
      }
      return;
    }
    // Saved, so none of these is an edit of the workflow any more. The band
    // goes with them, and with it whatever in it held focus.
    const held = document.activeElement;
    if (!held || held === document.body || bandBox.value?.contains(held)) waitInFooter();
    clearChanges();
    formKey.value = "";
    let at = from;
    try {
      if (saved.key !== from && props.source?.pickWorkflow) {
        // Before the picker moves: its list has no row for the new workflow.
        cards.value = (await listWorkflowCards()).cards;
        if (!here(from)) return;
      }
      activeKey.value = at = saved.key;
      if (!(await readWorkflow(at))) return;
    } catch (err) {
      console.warn(`[run] saved, but could not read ${saved.key} again`, err);
      if (!here(at)) return;
      loadFailed.value = `${saved.done} It could not be read again, so it cannot be run from here: close this and open it again.`;
      // The band's buttons went with the form: focus must not fall to the page.
      await nextTick();
      loadFailedLine.value?.focus?.();
      return;
    }
    bandNote.value = saved.done;
    // The band's buttons went with it: focus must not fall to the page. The
    // line it lands on is read then, so nothing else announces it.
    await nextTick();
    bandNoteLine.value?.focus?.();
    await runPreflight();
  } finally {
    saving.value = "";
  }
}

/** Save as new…: the band becomes its one question, the name. */
async function askName() {
  if (saving.value || submitting.value) return;
  // The picked checkpoint by the name its picker shows it under.
  const picked = checkpointEdited.value
    ? checkpointOptions.value.find((option) => option.value === checkpointValue.value)
        ?.label || shownModel(checkpointValue.value)
    : "";
  newName.value = suggestedName(card.value?.name, picked);
  bandError.value = "";
  naming.value = true;
  await nextTick();
  nameField.value?.focus?.();
  nameField.value?.select?.();
}

async function backFromName() {
  // The name is the save's while it is out, and its field still has the keys.
  if (saving.value) return;
  naming.value = false;
  await nextTick();
  asNewButton.value?.focus?.();
}

/**
 * Save as new: the same changes stored as a new workflow called *newName*,
 * and the popup moved onto it. The workflow it was opened on is left alone.
 */
async function saveAsNew() {
  const name = newName.value.trim();
  if (saving.value || submitting.value || !name || !band.value.count) return;
  await keep("new", async (key) => {
    const saved = await saveWorkflowAsNew(key, { name, changes: saveChanges.value });
    return {
      key: saved?.workflow_id || "",
      done: `Saved as ${saved?.name || name}.`,
    };
  });
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
    case "original":
      return "The picture this one was made from";
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
  // The selection goes to one input: a run told to send it elsewhere is
  // untold first, or the next body names two and is refused whole.
  for (const [other, pick] of Object.entries(picks)) {
    if (pick.picture_id === null) delete picks[other];
  }
  if (input.fill === "original") {
    // A replay's own picture stands here, ahead of the card's stored setup,
    // so nothing written to the card would move it: this run alone is told
    // (`picture_id: null` is "my selection goes here").
    picks[key] = {
      slot_label: input.slot_label,
      input_name: input.input_name,
      picture_id: null,
    };
    await settle(runPreflight());
    await focusRow(key);
    return;
  }
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
    loras: runChanges.value.loras,
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
    label: taskLabel(),
  };
  // Exactly one source, which the route checks before it reads anything: a
  // saved recipe names its own card, with pictures the card is `target`, and
  // without either it IS the source.
  // The graph's own loaders the owner took out. Only when there
  // are any: the route rewires around each, and a key it is not sent cannot
  // refuse a run over a feature nobody used.
  if (runChanges.value.skip_loras.length) {
    body.skip_loras = runChanges.value.skip_loras;
  }
  // LoRAs added in loaders of their own: only when there are any.
  if (addLorasBody.value.length) body.add_loras = addLorasBody.value;
  // The same for the stages the owner turned off (#1623), and the ones the
  // default recipe runs without that this run keeps (#1849).
  const off = stagesSet(runChanges.value, false);
  const on = stagesSet(runChanges.value, true);
  if (off.length) body.skip_stages = off;
  if (on.length) body.keep_stages = on;
  // The model rows, by the default recipe's loader address (#1623).
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
    // A picture's own recipe again ("Run recipe", "Make more like these…" on
    // one): the picture is the run's output, so its picture input is what the
    // picture was made FROM, not the picture. An edit, a workflow the owner
    // was asked to pick, or another workflow runs over the picture itself.
    if (!isEdit.value && !props.source?.pickWorkflow && runsOwnWorkflow.value) {
      body.replay = true;
    }
  } else {
    body.workflow_id = activeKey.value;
    // What waited in the Workflow tab as this form was laid from it, whatever
    // the owner undid of it here: a save made after the run clears that much
    // and keeps what was edited in the tab since. Unsent where it could not
    // be read, and the server reads it for itself.
    if (seen.value) body.waited = seen.value;
  }
  // The whole list whenever the form knows it, which the server takes over
  // what the LoRAs say. With no LoRA person and nobody added it is left out,
  // so the server reads the graph itself and still links a LoRA the form
  // could not name (a filename the shelf resolves, or attachments still
  // loading). A LoRA person taken off is still a LoRA person: that sends [].
  if (ofAdded.value.length || loraPeople.value.length) {
    body.character_ids = ofPeopleIds.value;
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
  // A parameter waiting in the Workflow tab that this form has no field for
  // is carried as it waits.
  const rows = [...carried.value.values];
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

/** `person` is the id of the person whose LoRA the row holds, or null. */
function addedRow(sha256, strength = 1, person = null) {
  addedKey += 1;
  return { key: `added-${addedKey}`, sha256, strength, person };
}

function removeAddedLora(index) {
  const person = addedLoras.value[index]?.person;
  // Taking a picked person's LoRA off is unpicking them.
  if (personIds.value.includes(person)) {
    pickPerson(person);
    // The × went with the row: the keyboard goes to the tile it unpicked,
    // which is where the pick is taken back.
    void focusLater(`[data-person="${person}"]`);
    return;
  }
  addedLoras.value.splice(index, 1);
  void focusAfterRow(addedLoras.value[index]);
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
  // A loader read off the workflow's chain comes with the shelf's digest.
  const sha256 =
    slot.sha256 ||
    (slot.by === "digest" ? graphValue : shelfDigestFor(graphValue));
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
    // Only a loader read off the workflow's chain says its CLIP strength,
    // and whether the graph had it as one number with the model's.
    baseClip: slot.strengths && "clip" in slot.strengths ? slot.strengths.clip : undefined,
    clipTied: Boolean(slot.strengths?.tied),
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
  return loraStem(row.graphValue) || "LoRA";
}

/**
 * The sentence under a graph row that will not load as written, or "".
 *
 * The pre-flight's word first — the run already leaves that loader out — and
 * then the shelf's, which cannot identify the file. The row's × is the fix
 * either way: the owner can run without it on purpose.
 */
function loraFlag(row) {
  if (row.added || row.skipped) return "";
  const file = loraBase(row.graphValue);
  if (file && bypassedFiles.value.has(file)) {
    return "Not in this ComfyUI. The run leaves this loader out.";
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
 * Take one of the GRAPH's loaders out (`skip_loras`).
 *
 * The run goes without it, and a save writes the workflow without it. The
 * row stays, says Removed, and Put back takes it back. The pre-flight is
 * asked again, because the answer changes: a loader nothing can be rewired
 * around comes back as `lora_not_skippable`, and a bypassed one is reported
 * as left out instead.
 */
async function skipGraphLora(row) {
  if (row.added || submitting.value) return;
  row.skipped = true;
  loraLive.value = `${rowName(row)} is removed. Put back is on the same row.`;
  await focusLoraRow(row.key, "back");
  await runPreflight();
}

async function useGraphLora(row) {
  row.skipped = false;
  loraLive.value = `${rowName(row)} is back.`;
  await focusLoraRow(row.key, "remove");
  await runPreflight();
}

/** Whether a loader of the graph holds another LoRA or strength than it had. */
function loraEdited(row) {
  return (
    !row.added &&
    !row.skipped &&
    // A loader the shelf cannot name has no digest to send it by, so it is
    // never a change the run carries or the band counts.
    Boolean(row.sha256) &&
    (row.sha256 !== row.baseSha || row.strength !== row.baseStrength)
  );
}

/** What the loader had, as its reset chip says it: the LoRA and its strength. */
function loraWas(row) {
  return `${rowName({ ...row, sha256: row.baseSha })} · ${row.baseStrength}`;
}

function resetLora(row) {
  row.sha256 = row.baseSha;
  row.strength = row.baseStrength;
}

/**
 * A row's options, with the graph's own unresolvable file among them.
 *
 * Without its current value in the list the select would render empty and read
 * as a slot nobody has filled, when in fact the graph fills it with a file the
 * shelf has never seen.
 */
function optionsFor(row) {
  // The same for a file this library hides: the workflow loads it, so the
  // row names it, though no other row is offered it.
  const held = adapterOptions.value.some((o) => o.value === row.sha256)
    ? null
    : adapters.value.find((a) => a.sha256 && a.sha256 === row.sha256);
  const options = held
    ? [adapterOption(held), ...adapterOptions.value]
    : adapterOptions.value;
  if (row.baseSha || !row.graphValue || row.added) return options;
  return [
    { value: "", label: `${row.graphValue} (not on your shelf)` },
    ...options,
  ];
}

function removeLora(index) {
  loras.value.splice(index, 1);
  void focusAfterRow(loras.value[index] || addedLoras.value[0]);
}

/**
 * A row's × took the row and itself away: the keyboard goes to the × of the
 * row that moved up into its place, else to Add LoRA, never to the page.
 */
async function focusAfterRow(next) {
  await nextTick();
  const root = formRoot.value;
  const target =
    (next && root?.querySelector(`[data-lora="${next.key}"] .rund-lora-act`)) ||
    root?.querySelector('[data-focus="add-lora"]');
  target?.focus?.();
}

/**
 * The default recipe's LoRA *lora* among *loaders* (chain loaders, or the
 * slots made of them): by the shelf's digest, or by file where either side
 * has none.
 */
function loaderOf(lora, loaders) {
  const digest = String(lora?.sha256 || "").toLowerCase();
  const file = loraBase(lora?.filename);
  return loaders.find((loader) => {
    const held = String(loader.sha256 || "").toLowerCase();
    if (digest && held) return digest === held;
    return Boolean(file) && loraBase(loader.filename ?? loader.value) === file;
  });
}

/**
 * The workflow's own loaders as LoRA rows, for a run of it by itself.
 *
 * A row for each loader holding a LoRA of the default recipe, at the
 * recipe's strength: that is what such a run loads. A loader the recipe
 * leaves out gets none (a run bypasses it, and one holding a person's LoRA is
 * never the workflow's, rule 4 of #1846). A workflow with no sample to take a
 * default from runs its graph as it is, so every loader is a row, less the
 * ones holding a LoRA attached to a person.
 */
function ownSlots(chain) {
  const recipeLoras = card.value?.default_recipe?.loras || [];
  const asGraph = !card.value?.default_recipe?.sampled && !recipeLoras.length;
  const ofPerson = new Set(
    [...adapters.value, ...unknownLoras.value]
      .filter((row) =>
        (row.attachments || []).some((entry) => entry.entity_type === "character"),
      )
      .map((row) => String(row.sha256 || "").toLowerCase()),
  );
  const loaders = [
    ...(chain?.loaders || []),
    ...(chain?.lanes || []).flatMap((lane) => lane.loaders || []),
  ];
  return loaders.flatMap((loader) => {
    const own = recipeLoras.find((lora) => loaderOf(lora, [loader]));
    const digest = String(loader.sha256 || "").toLowerCase();
    if (!own && (!asGraph || ofPerson.has(digest))) return [];
    return [
      {
        node_id: String(loader.node_id),
        field: String(loader.field || "lora_name"),
        by: "filename",
        value: loader.filename,
        sha256: digest,
        strengths: {
          model: own?.strength ?? loader.strength ?? 1,
          clip: loader.strength_clip ?? null,
          tied:
            loader.strength_clip == null ||
            loader.strength_clip === loader.strength,
        },
      },
    ];
  });
}

/**
 * Read what a run of the workflow by itself needs beside its card: its
 * loaders, and what waits on it in the Workflow tab with the version that
 * was made against. Neither holds the popup back: without the loaders there
 * are no LoRA rows, and without the other there is no band.
 */
async function loadOwn(key, ask) {
  chainSlots.value = [];
  changesState.value = null;
  if (!runsWorkflowItself.value || !key) return;
  loadModelChoices(key, ask);
  const [chain, state] = await Promise.all([
    getLoraChain(key).catch((err) => {
      console.warn(`[run] could not read the LoRA loaders of ${key}`, err);
      return null;
    }),
    getWorkflowChanges(key).catch((err) => {
      console.warn(`[run] could not read what waits on ${key}, so its changes cannot be saved from here`, err);
      return null;
    }),
  ]);
  if (ask !== readAsk) return;
  chainSlots.value = ownSlots(chain);
  changesState.value = state;
}

/**
 * Build the workflow's own LoRA rows and lay what waits in the Workflow tab
 * over the form, as if the owner had made those edits here (rule 7 of
 * #1846): the model picks, the loaders removed or set, the stages, the
 * parameters. The chips then show the SAVED value, the band counts them and
 * Run sends them. A waiting change the form has no control for is `carried`.
 * Nothing is written: closing leaves what waited exactly as it was.
 */
function layOwn() {
  loras.value = loraSlots.value.map((slot) => loraRow(slot));
  initialLoraCount.value = loras.value.length;
  // Edits the owner made on another workflow and brought along are theirs.
  const clean = JSON.stringify(formChanges.value) === JSON.stringify(noChanges());
  const waiting = whole(changesState.value?.waiting);
  const took = noChanges();
  const left = noChanges();
  for (const model of waiting.models) {
    const row = modelRows.value.find((entry) => entry.addresses.includes(model.address));
    const ours =
      checkpointRunModels.value.some((entry) => entry.address === model.address) ||
      Boolean(row);
    // Named by digest: the form holds files, so it is left as it waits.
    if (!ours || !model.filename) {
      left.models.push(model);
      continue;
    }
    if (row) {
      if (loraBase(model.filename) === loraBase(row.file)) delete modelEdits[row.key];
      else modelEdits[row.key] = model.filename;
    } else {
      setCheckpoint(model.filename, false);
    }
    took.models.push(model);
  }
  for (const item of waiting.loras) {
    const row = loraRowAt(item.node_id, item.field);
    if (!row) {
      left.loras.push(item);
      continue;
    }
    row.skipped = false;
    row.sha256 = String(item.sha256 || "").toLowerCase();
    if (item.strength_model != null) row.strength = item.strength_model;
    took.loras.push(item);
  }
  for (const item of waiting.skip_loras) {
    const row = loraRowAt(item.node_id, item.field);
    if (!row) {
      left.skip_loras.push(item);
      continue;
    }
    row.skipped = true;
    took.skip_loras.push(item);
  }
  for (const [name, on] of Object.entries(waiting.stages)) {
    if (!stageRows.value.some((row) => row.name === name)) {
      left.stages[name] = on;
      continue;
    }
    if (on === stageDefault(name)) delete stageChoice[name];
    else stageChoice[name] = on;
    took.stages[name] = on;
  }
  for (const value of waiting.values) {
    const field = fieldAt(value.slot_label, value.input_name);
    if (!field) {
      left.values.push(value);
      continue;
    }
    setValue(field, value.value);
    took.values.push(value);
  }
  laid.value = took;
  carried.value = left;
  seen.value = changesState.value && waiting;
  changesAtOpen.value = JSON.stringify(clean ? formChanges.value : noChanges());
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
  // Taken off on purpose, as a tile's press would: typing does not put them back.
  personIds.value.forEach((picked) => unpickedIds.add(picked));
  personIds.value = [];
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
  if (!key || !close()) return;
  void router?.push?.(editLorasRoute(key));
}

/**
 * "Open in Workflows": the card this popup runs, selected on the Workflows
 * screen with its rail open. The popup closes, as it does for Edit LoRAs…,
 * because the screen it opens on is behind it.
 */
function openInWorkflows() {
  const key = activeKey.value;
  if (!key || !close()) return;
  void router?.push?.({ name: "workflows", query: { workflow: key } });
}

/**
 * Create with LoRA: the LoRAs attached to the person or set, and the workflow
 * sets that rank the picker. The first attached LoRA starts as an added row,
 * and `followLora` swaps it for the one the workflow picked is for.
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
  // Offered, and the first one added: so not the ones this library hides.
  attachedLoras.value = kinds
    .flat()
    .filter((row) => row?.sha256 && !row.hidden);
  // A set hidden in this library does not rank the picker (#1869). No "Show
  // them" here, so the payload's own list decides.
  const hidden = new Set(sets?.hidden_sets || []);
  handMadeSets.value = (sets?.hand_made || []).filter(
    (set) => !hidden.has(`hand:${set.id}`),
  );
  const first = attachedLoras.value[0];
  addedLoras.value = first
    ? [
        addedRow(
          first.sha256,
          1,
          source.entityType === "character" ? Number(source.entityId) : null,
        ),
      ]
    : [];
}

/**
 * The people and the shelf's unclassified files, for the person picker and
 * "Pictures are of"; neither read holds the popup up.
 */
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
async function loadCard(key, ask, { keepEdits = false } = {}) {
  formOptions.value = {};
  formNodeNames.value = {};
  // Beside the card, never ahead of it: the options only turn a typed box
  // into a list, so a ComfyUI that does not answer leaves the boxes typed.
  getWorkflowFormInputs(key)
    .then((body) => {
      if (ask === readAsk) {
        formOptions.value = optionsByAddress(body.nodes);
        formNodeNames.value = nodesByAddress(body.nodes);
      }
    })
    .catch((err) => {
      console.warn(`[run] could not read the choices of ${key}`, err);
    });
  const detail = await getWorkflowCard(key);
  // A read the picker has since left writes nothing: two moves in a row
  // answer in either order, and the slower must not put its card under the
  // faster's key.
  if (ask !== readAsk) return;
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
  // A model row is one workflow's loader: its pick names nothing on another.
  const droppedModels = modelRows.value
    .filter((row) => row.key in modelEdits)
    .map((row) => row.label);
  for (const key2 of Object.keys(modelEdits)) delete modelEdits[key2];
  if (!keepEdits) {
    card.value = next;
    fellBack.value = [];
    checkpointEdit.value = null;
    return;
  }
  const addresses = new Set((next?.defaults || []).map(address));
  const lost = Object.keys(edits).filter((key2) => !addresses.has(key2));
  fellBack.value = [
    ...lost.map((key2) => editedLabels[key2] || key2),
    ...droppedModels,
  ];
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

/** Bumped per pre-flight, so only the newest one's answer is kept. */
let preflightAsk = 0;

async function runPreflight() {
  // Asked on every edit, around every switch and after every save, and
  // answered in any order: an older answer is about a form that has moved on,
  // and nothing of it (the refusals, the picture rows a pin is written from,
  // the notices, the prompt, a checkpoint offer) may land over a newer one's.
  const ask = (preflightAsk += 1);
  const mine = () => ask === preflightAsk;
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
      await runPreflight();
      return;
    }
    reasons.value = all;
    // Asked again once a file is on the shelf: the swap onto the shelf's
    // loader is what makes it not missing.
    pickUp(
      all.flatMap((reason) =>
        reason?.code === "missing_models"
          ? (reason.models || []).map((model) => String(model.file))
          : [],
      ),
    ).then((added) => {
      if (added && askedFor === activeKey.value) void runPreflight();
    });
    offerCheckpoints(all, askedFor).catch((err) =>
      console.warn(`[run] could not offer checkpoints for ${askedFor}`, err),
    );
    bypassed.value = (answer?.groups || []).flatMap((group) => [
      ...repairNotices(group),
      ...unplacedNotice(group),
    ]);
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
  const ask = ++readAsk;
  // The open's own writes of the form are a read's, like any other.
  const mine = () => ask === readAsk;
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
    followLora(key);
    if (key) await loadCard(key, ask);
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
    if (!mine()) return;
    // Every run reads them: "Pictures are of" lists the people, and says
    // whose LoRA the run loads.
    void loadPeople();
    if (runsWorkflowItself.value && key) {
      await loadOwn(key, ask);
      if (!mine()) return;
    }
    layOwn();
    formKey.value = key;
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
    // The open's, not the read's: a read that replaced it does not end it.
    loading.value = false;
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

/**
 * Close, unless a run or a band save is out: either is this popup's work, and
 * closing over it would leave its answer nowhere to land. Every close is made
 * here but the run's own, which is that answer. True when it closed.
 */
function close() {
  if (submitting.value || saving.value) return false;
  emit("close");
  return true;
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
  if (close()) event.stopPropagation();
}

/**
 * The desktop shell's bridge, when there is one: its window opens ComfyUI's
 * plain `http:` only through `desktop:openComfyui`, as on the Workflow tab.
 */
const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;
const filterStore = useFilterStore();
/** Whether there is a ComfyUI to open, and a way to open it from here. */
const canOpenComfyui = computed(
  () => Boolean(filterStore.comfyuiUrl) && (!desktop || !!desktop.openComfyui),
);
/**
 * ComfyUI answered that its ComfyUI-PixlStash nodes are from before they
 * opened a run (`open_run.js`): the link would open whatever ComfyUI had last.
 * Only a definite no refuses; a ComfyUI that cannot be asked is left to try.
 */
const comfyuiOpensNoRuns = ref(false);
// Asked per address, as the Workflow tab asks: the address can arrive after
// the popup does (the store still loading, or Settings opened from a notice
// here), and the answer about one ComfyUI says nothing of the next.
let nodeCheck = 0;
watch(
  () => canOpenComfyui.value && filterStore.comfyuiUrl,
  async (url) => {
    const check = ++nodeCheck;
    comfyuiOpensNoRuns.value = false;
    if (!url) return;
    try {
      const node = await getPixlstashNode();
      if (check !== nodeCheck) return;
      comfyuiOpensNoRuns.value = node?.opens_runs === false;
    } catch (err) {
      console.warn("[run] could not ask ComfyUI for the PixlStash nodes", err);
    }
  },
  { immediate: true },
);
const opening = ref(false);
const openError = ref("");
/** Why Open in ComfyUI does nothing, or "". */
const openRefusal = computed(() => {
  if (comfyuiOpensNoRuns.value) {
    return "Update the ComfyUI-PixlStash nodes in ComfyUI, then restart it, to open a run there.";
  }
  return seedError.value;
});
const openTip = computed(
  () =>
    openRefusal.value ||
    "Opens the workflow with these settings in it. Saving it in ComfyUI makes a new workflow.",
);

/**
 * Open ComfyUI on this run: the workflow with what the form holds in it.
 *
 * A link cannot carry a run, so the server builds its graph and holds it
 * (`POST /workflows/run/opening`, the run's own body), and ComfyUI is sent to
 * fetch it by the id that answers. What ComfyUI saves from there comes back
 * as a new workflow; the popup stays open, since the run is still to make.
 *
 * In a browser the tab is opened BEFORE the request and sent on after it: a
 * `window.open` after an await is what popup blockers refuse. That needs the
 * window's handle, so it is opened without `noopener` and cut loose
 * (`opener = null`) before it goes anywhere.
 */
async function openInComfyui() {
  if (openRefusal.value || opening.value) return;
  openError.value = "";
  // Read once: the address can change while the request is out (Settings
  // opens from a notice here), and the tab goes where this press checked.
  const address = filterStore.comfyuiUrl;
  // Before anything is asked of the server: the request uploads pictures.
  if (!comfyuiLink(address, {})) {
    openError.value = "The ComfyUI address in Settings is not a web address.";
    return;
  }
  const tab = desktop?.openComfyui ? null : window.open("", "_blank");
  if (!tab && !desktop?.openComfyui) {
    openError.value =
      "The browser blocked the new tab. Allow pop-ups for PixlStash, then try again.";
    return;
  }
  if (tab) tab.opener = null;
  opening.value = true;
  try {
    // One graph opens however many runs the form asks for, so a count the
    // form is still refusing does not stop it.
    const held = await openWorkflowRun({ ...runBody(), count: 1 });
    const link = comfyuiLink(address, {
      pixlstash_run: held.opening,
      pixlstash_of: held.workflow_id,
    });
    if (tab) tab.location.replace(link);
    else if (!(await desktop.openComfyui(link))) {
      throw new Error("Could not open ComfyUI.");
    }
  } catch (err) {
    tab?.close();
    console.warn("[run] could not open this run in ComfyUI", err);
    openError.value = errorMessage(err, "Could not open this run in ComfyUI.");
  } finally {
    opening.value = false;
  }
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
    // What this run changed, in the band's own words, for the Tasks tab to
    // offer once the pictures are back. A run that changed nothing, or a
    // recipe's run, is the workflow's next run all the same: it takes down
    // what an earlier one offered.
    runDialog.ranWith(
      activeKey.value,
      band.value.count
        ? {
            name: card.value?.name || "",
            count: band.value.count,
            text: band.value.text,
            promptIds: prompts.map((entry) => String(entry?.prompt_id)),
          }
        : null,
    );
    emit("run", {
      prompts,
      pictureIds: pictureIds.value,
      // Only on a `partial` answer: why the rest of the batch was not queued.
      ...(answer?.error ? { error: answer.error } : {}),
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

.rund-cover {
  position: relative;
}

/* A neutral photo chip (design system Badge, `subtle`): it labels, it is not
   an action or a selection, so it takes no amber and no olive. */
.rund-example {
  position: absolute;
  top: var(--space-2);
  left: var(--space-2);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
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

/* "Pictures are of": one row a person, field-styled like the select under it,
   so the list reads as the control it replaced. */
.rund-of,
.rund-of-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rund-of-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.rund-of-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
}

/* Taken off: still there, so the gap shows, and plainly not linked. */
.rund-of-row--off {
  border-style: dashed;
  background: transparent;
}

.rund-of-row--off .rund-of-name {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-decoration: line-through;
}

.rund-of-row--off .rund-of-face {
  opacity: 0.45;
}

.rund-of-row--none {
  border-style: solid;
  background: transparent;
}

.rund-of-face {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--space-6);
  height: var(--space-6);
  border-radius: 50%;
  object-fit: cover;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rund-of-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: var(--leading-tight);
}

.rund-of-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: var(--weight-medium);
}

.rund-of-why {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
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
  /* The × column, held at the width the design drew it. */
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

/* A slot the run cannot go without: the glow of the landing pulse
   (`gridNewPulse`), held, so it outweighs the cover beside it. */
.rund-in-tile--needed:not(.rund-in-tile--off) {
  border-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-surface));
  box-shadow:
    0 0 10px rgba(var(--v-theme-accent), 0.5),
    0 0 18px rgba(var(--v-theme-accent), 0.25);
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

/* The Pinned count: a raspberry pill, the charts' hue. As text the raspberry
   reads 2.3:1 on the dark panel, so it is the fill and on-secondary the label
   (4.91:1 in both themes). */
.rund-count {
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

.rund-quiet,
.rund-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.rund-adding {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.rund-note {
  margin: 0;
  line-height: var(--leading-body);
}

/* A graph row taken out: its name, struck, the word saying so, and Put back
   at the end of the row. */
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

/* The last column holds a row's ×, at its end. */
.rund-lora-act {
  justify-self: end;
}

/* Put back is a word, wider than the × column: it takes the strength column
   too, which a removed row has nothing in. */
.rund-lora--skipped .rund-lora-skip-line {
  grid-column: 1;
}

.rund-lora-back {
  grid-column: 2 / 4;
  justify-self: end;
}

/* What a changed loader had: the reset chip, on a line of its own under the
   row so the row's three columns do not move. */
.rund-lora-was {
  display: flex;
}

/* ── Save what you ran ──────────────────────────────────────────────────
   A panel across both columns, under the form and above the footer. The
   list of what changed is cut, never wrapped, so the band is one height
   however much has changed: its coming and going moves the footer by
   exactly that, once, and not again as the owner types. */
.rund-band {
  /* Readable at rest: the form is taller than the window more often than
     not, and a band at the foot of a scrolled body is a count nobody sees.
     It holds to the bottom of the body's scrollport, over the form, and is
     in its own place once the form is scrolled to its end. */
  position: sticky;
  bottom: 0;
  z-index: var(--z-sticky);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
}

/* The name question is a labelled field: its buttons sit on the field's own
   line, not on the middle of the label and the field together. */
.rund-band--naming {
  align-items: flex-end;
}

/* The body's own bottom padding is under a held band, and the form would
   show through it as a sliver: painted over in the dialog's surface, from
   under the band's border to a pixel past the padding, since the band can
   sit on a half pixel and a seam one pixel high shows the tops of letters. */
.rund-band::after {
  content: "";
  position: absolute;
  top: 100%;
  right: 0;
  left: 0;
  height: calc(var(--space-5) + 1px);
  margin-top: 1px;
  background: rgb(var(--v-theme-surface));
}

.rund-band-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: var(--text-sm);
}

.rund-band-text b {
  font-weight: var(--weight-semibold);
}

.rund-band-list,
.rund-band-stays {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

/* One line: the whole of it is in its `title` when it is cut. What stays
   with the run is never cut; it is one line unless a LoRA was added by hand,
   which is not something typing changes. */
.rund-band-list {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rund-band-name {
  flex: 1;
  min-width: 0;
}

/* The server's reason for a closed Save, and a refusal: under the row. */
.rund-band-why {
  flex-basis: 100%;
}

.rund-band-done {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
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

/* One line at any width the popup has: nothing wraps, the glyph and the words
   keep their size, and the buttons give way, each to an ellipsis. */
.rund-trigger {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.rund-trigger-text {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  white-space: nowrap;
}

/* A flex item's `min-width: auto` is what would hold a button at its word. */
.rund-trigger-add {
  flex: 0 1 auto;
  min-width: 0;
}

.rund-trigger-add :deep(.app-btn__label) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
