<template>
  <!-- An outfit's sheet is 4:5 portrait, the others 5:3. -->
  <div class="cs-split" :class="{ 'cs-split--portrait': kind === 'outfit' }">
    <div ref="mainPane" class="cs-pane" data-testid="cs-main">
      <template v-if="phase === 'describe'">
        <div v-if="item" class="cs-step-h">
          <h3>{{ item.describe(name) }}</h3>
          <span>{{ item.intro(name) }}</span>
        </div>
        <div v-else class="cs-step-h">
          <h3>Describe {{ name }} and draw sheets</h3>
          <span
            >Each sheet is one person drawn six times: four passport photos
            (front, three-quarter smiling, profile, three-quarter laughing) and
            two body photos. You pick the sheet that looks right.</span
          >
        </div>

        <p
          v-if="likenessNotice && !fromReference && !item"
          class="cs-notice"
          data-testid="cs-likeness"
        >
          <v-icon size="16">{{
            chosenLora ? "mdi-account-check-outline" : "mdi-alert-outline"
          }}</v-icon>
          <span>{{ likenessNotice }}</span>
        </p>
        <p
          v-if="workflowsLoaded && !modeCards.length"
          class="cs-notice cs-notice--bad"
          data-testid="cs-no-workflow"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span
            >{{
              fromReference
                ? "Sheets from a reference need a Qwen-Image-Edit, FLUX.2 Klein, MiniMax H3 or MiniMax H3 Image sheet workflow, and the library has none."
                : item
                  ? `${item.title} need a MiniMax H3 Image, Krea 2, Qwen-Image, FLUX.2 Klein or MiniMax H3 text-to-image workflow, and the library has none. Import one from Workflows, then come back.`
                  : "Sheets need a MOSS-Image, Krea 2, Qwen-Image, FLUX.2 Klein, MiniMax H3 or MiniMax H3 Image text-to-image workflow, and the library has none. Import one from Workflows, then come back."
            }}</span
          >
        </p>
        <!-- The workflow itself is chosen in the screen's toolbar. -->
        <p
          v-if="workflowProblem"
          class="cs-notice cs-notice--bad"
          data-testid="cs-workflow-problem"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span
            >{{ workflowProblem
            }}<button
              v-if="sizeless"
              type="button"
              class="cs-link cs-link--inline"
              :disabled="working"
              data-testid="cs-convert-chosen"
              @click="makeSheetWorkflow(sizeless)"
            >
              Make a sheet copy that can
            </button></span
          >
        </p>

        <div v-if="!item" class="cs-lora">
          <AppSelect
            v-model="loraSha"
            label="Character LoRA · optional"
            @update:model-value="picked.add('lora')"
            :options="loraOptions"
            data-testid="cs-lora"
          />
          <div v-if="chosenLora" class="cs-sl">
            <span class="cs-sl__lbl">Strength</span>
            <input
              v-model.number="strength"
              class="cs-range"
              type="range"
              min="0.1"
              max="1.5"
              step="0.05"
              aria-label="LoRA strength"
              @input="picked.add('strength')"
            />
            <span class="cs-sl__val">{{ strength.toFixed(2) }}</span>
          </div>
          <span v-if="chosenLora && trigger" class="cs-hint"
            >Loaded into the sheet workflow, with the trigger word
            <b>{{ trigger }}</b> added to every sheet prompt. Only LoRAs for
            {{ engine.name }}, the sheet workflow's model, can be chosen.</span
          >
          <span v-else-if="chosenLora" class="cs-hint"
            >Loaded into the sheet workflow. Only LoRAs for {{ engine.name }},
            the sheet workflow's model, can be chosen.</span
          >
          <span v-else-if="fromReference" class="cs-hint"
            >Without a LoRA, the reference picture alone carries the face.</span
          >
          <span v-else class="cs-hint"
            >Without a LoRA, each sheet is a slightly different person, so you
            pick one.</span
          >
        </div>

        <!-- The description takes whatever height the form leaves. -->
        <div class="cs-fld cs-fld--grow">
          <AppTextarea
            v-model="form.description"
            label="Description"
            :rows="2"
            :placeholder="
              item
                ? item.descriptionPlaceholder
                : 'Adult woman, dark hair in a low bun, brown eyes, freckles, slim build'
            "
            data-testid="cs-description"
          />
          <span class="cs-hint">{{ descriptionHint }}</span>
        </div>
        <!-- An item's name: the set its panels go in. The description says
             the rest. -->
        <RecentInput
          v-if="item"
          v-model="form.name"
          label="Name"
          :placeholder="item.newName"
          data-testid="cs-item-name"
        />
        <!-- Each offers what earlier sheets were drawn with, the last one
             filled in. -->
        <template v-else>
        <div class="cs-grid2">
          <RecentInput
            v-model="form.top"
            label="Top"
            placeholder="a plain oatmeal crew-neck sweater"
            :options="offered.top"
            data-testid="cs-top"
          />
          <!-- `trousers` in the prompt: whatever covers the hips and thighs. -->
          <RecentInput
            v-model="form.trousers"
            label="Bottoms"
            placeholder="dark straight jeans · a pleated midi skirt"
            :options="offered.trousers"
          />
        </div>
        <RecentInput
          v-model="form.background"
          label="Background"
          placeholder="a plain light-grey studio · a sunlit park"
          :options="offered.background"
          data-testid="cs-background"
        />
        <RecentInput
          v-if="form.framing === 'full_body'"
          v-model="form.shoes"
          label="Shoes"
          placeholder="white sneakers"
          :options="offered.shoes"
        />
        </template>
        <!-- Photo look, wherever the server offers it for sheets: it replaces
             the Natural/Off switch below. On, the run adds its lines and the
             sheet prompt none of its own; off, neither adds a look. -->
        <PromptAssistGroup
          v-if="!item && layout === 'prose' && sheetAssist"
          :assist="sheetAssist"
          :model="sheetLook"
          @change="askSheetAssist"
        />
        <!-- Until the chosen workflow answers its check the group's place is held,
             so the step does not open on the switch below and then swap it. -->
        <div
          v-else-if="!item && sheetLookPending"
          class="cs-look-pending"
          aria-hidden="true"
          data-testid="cs-look-pending"
        />
        <!-- The choices of how a sheet is drawn: toggles side by side,
             wrapping only on a narrow window, with one line of hints under
             them. -->
        <div class="cs-fld">
          <div class="cs-opts">
            <div v-if="!item" class="cs-opt">
              <FieldLabel>Body photos</FieldLabel>
              <Segmented
                v-model="form.framing"
                aria-label="Body photos"
                :options="FRAMINGS"
                data-testid="cs-framing"
                @pick="framingTouched = true"
              />
            </div>
            <div class="cs-opt">
              <FieldLabel>Sheets to draw</FieldLabel>
              <Segmented
                v-model="form.count"
                aria-label="Sheets to draw"
                :options="COUNTS"
              />
            </div>
            <div v-if="!item" class="cs-opt">
              <FieldLabel>Sheet size</FieldLabel>
              <!-- A pick: a click, even on the size shown, or the arrows. -->
              <Segmented
                v-model="form.size"
                aria-label="Sheet size"
                :options="sizeOptions"
                data-testid="cs-size"
                @pick="picked.add('size')"
                @update:model-value="picked.add('size')"
              />
            </div>
            <div v-if="!item" class="cs-opt">
              <FieldLabel>Proportions</FieldLabel>
              <Segmented
                v-model="form.proportions"
                aria-label="Proportions"
                :options="PROPORTIONS"
                data-testid="cs-proportions"
                @pick="proportionsTouched = true"
              />
            </div>
            <div
              v-if="!item && layout === 'prose' && !sheetAssist && !sheetLookPending"
              class="cs-opt"
            >
              <FieldLabel>Photo look</FieldLabel>
              <Segmented
                v-model="form.photo_look"
                aria-label="Photo look"
                :options="PHOTO_LOOKS"
                data-testid="cs-photo-look"
              />
            </div>
            <div v-if="!item && sheetSamplerChoice" class="cs-opt">
              <FieldLabel>Sampler</FieldLabel>
              <Segmented
                v-model="sampler"
                aria-label="Sampler"
                :options="samplerOptions"
                data-testid="cs-sampler"
              />
            </div>
          </div>
          <span v-if="!item" class="cs-hint" data-testid="cs-framing-hint">{{
            framingHint
          }}</span>
          <span class="cs-hint"
            >About a minute a sheet. {{ sizeHint }}</span
          >
          <span
            v-if="!item && layout === 'prose' && !sheetAssist && !sheetLookPending"
            class="cs-hint"
            >Photo look asks for plain, unretouched photos rather than a
            polished render.</span
          >
          <span
            v-if="!item && sheetSamplerChoice"
            class="cs-hint"
            data-testid="cs-sampler-hint"
            >{{ SAMPLER_HINT }}</span
          >
        </div>
      </template>

      <template v-else>
        <div class="cs-step-h">
          <h3>Keep the sheets that look right</h3>
          <span>{{ pickIntro }}</span>
        </div>
        <p
          v-if="noneHold"
          class="cs-notice cs-notice--warn"
          data-testid="cs-none-hold"
        >
          <v-icon size="16">mdi-alert-outline</v-icon>
          <span v-if="item"
            >None of the {{ ended.length }} sheets has four {{ item.views }}
            that agree. Drawing more usually finds one. A shorter description
            with fewer competing details helps too.</span
          >
          <span v-else
            >None of the {{ ended.length }} sheets shows one person in all six
            photos. Drawing more usually finds one. A shorter description with
            fewer competing details helps too.</span
          >
        </p>
        <p
          v-if="noneClose"
          class="cs-notice cs-notice--warn"
          data-testid="cs-none-close"
        >
          <v-icon size="16">mdi-alert-outline</v-icon>
          <span>{{ noneClose }}</span>
        </p>
        <p
          v-if="failures.length"
          class="cs-notice cs-notice--bad"
          data-testid="cs-failed"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span
            >{{ failures.length }} of the runs failed: {{ failures[0] }}</span
          >
        </p>
        <ul class="cs-sheets">
          <li v-for="slot in slots" :key="slot.key" :data-slot="slot.key">
            <template v-if="slot.sheet">
              <div class="cs-tile">
                <button
                  type="button"
                  class="cs-sheetimg"
                  :class="{ 'cs-sheetimg--on': isKept(slot.sheet) }"
                  :aria-pressed="isKept(slot.sheet)"
                  :aria-label="`Sheet ${slot.n}: ${sheetWords(slot.sheet)}${photoWords(slot.sheet)}`"
                  :aria-keyshortcuts="'Z'"
                  :disabled="sheetLocked(slot.sheet)"
                  data-testid="cs-sheet"
                  @click="toggleKeep(slot.sheet.picture_id)"
                  @dblclick="openZoom(slot.sheet.picture_id)"
                  @wheel="onTileWheel($event, slot.sheet.picture_id)"
                  @keydown.z.exact="openZoom(slot.sheet.picture_id)"
                >
                  <img
                    :src="pictureThumbnailUrl(slot.sheet.picture_id)"
                    alt=""
                    loading="lazy"
                  />
                  <span class="cs-sheetimg__rk">{{ slot.n }}</span>
                  <!-- Each photo's own score, under its face. -->
                  <span
                    v-for="photo in photoScores(slot.sheet)"
                    :key="photo.panel"
                    class="cs-pscore"
                    :class="{ 'cs-pscore--off': !photo.ok }"
                    :style="photo.style"
                    aria-hidden="true"
                    data-testid="cs-photo-score"
                    >{{ photo.value.toFixed(2) }}</span
                  >
                  <span
                    v-if="isKept(slot.sheet)"
                    class="cs-sheetimg__ck"
                    aria-hidden="true"
                    ><v-icon size="14">mdi-check</v-icon></span
                  >
                </button>
                <!-- Zoom has its own control: a click on the sheet ticks it. -->
                <button
                  type="button"
                  class="cs-mag"
                  data-testid="cs-zoom-open"
                  :data-sheet="slot.sheet.picture_id"
                  :aria-label="`Zoom in on sheet ${slot.n}`"
                  @click="openZoom(slot.sheet.picture_id)"
                >
                  <v-icon size="16">mdi-magnify-plus-outline</v-icon>
                </button>
              </div>
              <span
                v-if="drawnFrom && slot.sheet.closeness != null"
                class="cs-ccap"
                data-testid="cs-closeness"
              >
                <span class="cs-cline"
                  ><b>Sheet {{ slot.n }}</b
                  ><span class="cs-meter" aria-hidden="true"
                    ><span :style="{ width: meterWidth(slot.sheet) }" /></span
                  ><span class="cs-num">{{
                    slot.sheet.closeness.toFixed(2)
                  }}</span
                  ><span>{{ closeWords(slot.sheet) }}</span></span
                >
                <span class="cs-cap">{{ slot.sheet.agreement_words }}</span>
              </span>
              <span v-else class="cs-cap"
                ><b>Sheet {{ slot.n }}</b> · {{ sheetWords(slot.sheet) }}</span
              >
            </template>
            <span
              v-else
              class="cs-sheetimg cs-sheetimg--wait"
              data-testid="cs-slot"
            >
              <span>{{ slot.label }}</span>
              <span
                v-if="slot.percent != null"
                class="cs-progress"
                role="progressbar"
                aria-label="Drawing"
                :aria-valuenow="slot.percent"
                aria-valuemin="0"
                aria-valuemax="100"
                ><span :style="{ width: `${slot.percent}%` }"
              /></span>
              <!-- This sheet's run alone; the others keep drawing. -->
              <button
                v-if="slot.stoppable"
                type="button"
                class="cs-link cs-stop"
                data-testid="cs-stop-run"
                :aria-label="`Stop generation of sheet ${slot.n}`"
                @click="stopSheetRun(slot.run)"
              >
                Stop generation
              </button>
            </span>
          </li>
        </ul>
      </template>
    </div>

    <aside
      class="cs-pane cs-pane--side"
      :class="{ 'cs-pane--wide': referenceCards.length || drawnFrom }"
      data-testid="cs-rail"
    >
      <template v-if="phase === 'describe' && referenceCards.length">
        <div class="cs-fld">
          <FieldLabel>Draw from</FieldLabel>
          <Segmented
            v-model="mode"
            aria-label="Draw from"
            :options="MODES"
            full
            data-testid="cs-mode"
            @pick="picked.add('mode')"
            @update:model-value="picked.add('mode')"
          />
        </div>
        <template v-if="fromReference">
          <img
            v-if="referenceId"
            class="cs-cbig"
            :src="pictureThumbnailUrl(referenceId)"
            alt="The reference picture"
            data-testid="cs-reference"
          />
          <div class="cs-cstrip" role="group" aria-label="Reference picture">
            <button
              v-for="(id, index) in referenceIds"
              :key="id"
              type="button"
              class="cs-reft"
              :class="{ 'cs-reft--on': id === referenceId }"
              :aria-pressed="id === referenceId"
              :aria-label="`${name}'s ${ORDINALS[index]} reference face`"
              data-testid="cs-reference-choice"
              @click="referenceId = id"
            >
              <img :src="pictureThumbnailUrl(id)" alt="" />
            </button>
            <button
              ref="pickButton"
              type="button"
              class="cs-reft cs-reft--add"
              aria-label="Pick another picture"
              data-testid="cs-reference-pick"
              @click="pickerOpen = true"
            >
              <v-icon size="14">mdi-image-plus-outline</v-icon>
            </button>
          </div>
          <span class="cs-hint">{{ referenceHint }}</span>
          <p
            v-if="!item"
            class="cs-notice cs-notice--warn"
            data-testid="cs-reference-warning"
          >
            <v-icon size="16">mdi-alert-outline</v-icon>
            <span
              >These sheets are drawn to look like the person in the picture.
              Use a real person's picture only with their agreement.</span
            >
          </p>
        </template>
      </template>
      <div
        v-else-if="phase === 'describe' && convertible"
        class="cs-item"
        data-testid="cs-convert"
      >
        <span
          >Sheets can also be drawn from a reference picture, with a copy of a
          Qwen-Image-Edit or FLUX.2 Klein workflow that draws at the sheet's
          size.</span
        >
        <button
          type="button"
          class="cs-link cs-link--start"
          :disabled="working"
          @click="makeSheetWorkflow()"
        >
          Make a sheet workflow from {{ convertible.name }}
        </button>
      </div>
      <div
        v-if="phase === 'describe' && !fromReference && textSource && !item"
        class="cs-item"
        data-testid="cs-convert-text"
      >
        <span
          >Any other text-to-image workflow can draw sheets too, through a copy
          that keeps each photo in its place on the sheet. The copy needs an
          up-to-date ComfyUI-PixlStash in ComfyUI.</span
        >
        <AppSelect
          v-if="textConvertibles.length > 1"
          v-model="textSourceId"
          label="Copy"
          :options="textConvertibleOptions"
          compact
        />
        <button
          type="button"
          class="cs-link cs-link--start"
          :disabled="working"
          @click="makeTextSheetWorkflow(textSource)"
        >
          Make a sheet workflow from {{ textSource.name }}
        </button>
      </div>
      <template v-if="phase !== 'describe' && drawnFrom">
        <h3 class="cs-secl">Drawn from</h3>
        <img
          class="cs-cbig"
          :src="pictureThumbnailUrl(drawnFrom)"
          alt="The reference picture"
          data-testid="cs-drawn-from"
        />
        <span class="cs-cline"
          ><span>{{ referenceWords(drawnFrom) }}</span
          ><button
            type="button"
            class="cs-link"
            :disabled="working || phase === 'filing'"
            @click="phase = 'describe'"
          >
            Draw from another
          </button></span
        >
        <!-- One picture is one look; the cluster is every look of them. -->
        <div v-if="referenceIds.length" class="cs-fld">
          <FieldLabel>Compare with</FieldLabel>
          <Segmented
            v-model="compareTo"
            aria-label="Compare the sheets with"
            :options="COMPARE_WITH"
            full
            data-testid="cs-compare-to"
          />
        </div>
      </template>
      <h3 v-if="!railIsReference" class="cs-secl">
        {{ item ? `This ${item.noun}` : `${name} so far` }}
      </h3>
      <h3 v-else-if="keptSheets.length" class="cs-secl">Kept</h3>
      <div class="cs-rail">
        <ul
          v-if="keptSheets.length"
          class="cs-keptgrid"
          aria-label="Kept sheets"
          data-testid="cs-kept"
        >
          <li v-for="sheet in keptSheets" :key="sheet.picture_id">
            <img
              class="cs-rail__sheet"
              :src="pictureThumbnailUrl(sheet.picture_id)"
              alt=""
            />
            <b>Sheet {{ sheet.n }}</b>
          </li>
        </ul>
        <div v-if="referenceIds.length && !railIsReference" class="cs-item">
          <span class="cs-faces">
            <img
              v-for="id in referenceIds"
              :key="id"
              :src="pictureThumbnailUrl(id)"
              alt=""
            />
          </span>
          <span
            ><b>{{ name }}'s reference faces</b>. New sheets are ranked by how
            close they come to these.
            <button
              type="button"
              class="cs-link cs-link--inline"
              data-testid="cs-change-faces"
              @click="emit('faces', $event)"
            >
              Change…
            </button></span
          >
        </div>
        <div v-if="!railIsReference" class="cs-item">
          <span class="cs-row1"
            ><b>Description</b>
            <button
              v-if="phase !== 'describe'"
              type="button"
              class="cs-link"
              :disabled="working || phase === 'filing'"
              @click="phase = 'describe'"
            >
              Edit
            </button></span
          >
          <span class="cs-desc">{{
            form.description.trim() || "No description yet."
          }}</span>
        </div>
      </div>
      <div class="cs-gapbox">
        <h4 class="cs-secl">Still to come</h4>
        <div class="cs-todo">
          <span v-if="phase === 'describe'"
            ><span class="cs-n">1</span
            ><span
              ><b>Sheets</b> ·
              {{
                item
                  ? `2–4 sheets of four ${item.views}, keep one`
                  : "2–4 sheets of six photos, pick one"
              }}</span
            ></span
          >
          <span v-if="item"
            ><span class="cs-n">2</span
            ><span
              ><b>The {{ item.noun }}</b> · its
              {{ item.angles.slice(0, -1).join(", ") }} and
              {{ item.angles.at(-1) }}, cut from the sheet</span
            ></span
          >
          <span v-else
            ><span class="cs-n">2</span
            ><span
              ><b>Pick the set</b> · new and existing pictures, ranked
              together</span
            ></span
          >
        </div>
      </div>
    </aside>

    <ZoomPopout
      :open="!!zoomed"
      :src="zoomed ? sheetUrl(zoomed) : ''"
      :alt="zoomed ? `Sheet ${zoomed.n}` : ''"
      :caption="
        zoomed ? `Sheet ${zoomed.n} · ${sheetWords(zoomed)}${photoWords(zoomed)}` : ''
      "
      :has-prev="hasPrev"
      :has-next="hasNext"
      :entry="zoomEntry"
      @close="closeZoom"
      @prev="stepZoom(-1)"
      @next="stepZoom(1)"
    >
      <template v-if="drawnFrom" #aside>
        <div class="cs-zoomref">
          <span class="cs-secl">Drawn from</span>
          <img
            class="cs-cbig"
            :src="pictureThumbnailUrl(drawnFrom)"
            alt="The reference picture"
          />
        </div>
      </template>
      <!-- The six photos using the sheet cuts, so the cut is seen before
           it is made; dashed amber when it found no clean line somewhere.
           The person can drag them: a double line in the accent colour once
           edited. -->
      <template v-if="zoomedCut.length" #over>
        <div
          v-for="(cell, i) in zoomedCut"
          :key="i"
          class="cs-cell"
          :class="{
            'cs-cell--unclean':
              !boxEdited(zoomed, i) && zoomed.cuts_cleanly === false,
            'cs-cell--edited': boxEdited(zoomed, i),
            'cs-cell--editable': !cutLocked,
          }"
          :style="cellStyle(cell)"
          :tabindex="cutLocked ? undefined : 0"
          :role="cutLocked ? undefined : 'group'"
          :aria-label="
            cutLocked
              ? undefined
              : `${panelName(i)}${boxEdited(zoomed, i) ? ', edited' : ''}`
          "
          :aria-hidden="cutLocked ? 'true' : undefined"
          :aria-describedby="cutLocked ? undefined : 'cs-cut-hint'"
          data-testid="cs-cell"
          @pointerdown="beginDrag($event, zoomed, i, null)"
          @pointermove="moveDrag"
          @pointerup="endDrag"
          @pointercancel="endDrag"
          @dblclick.stop
          @keydown="onCellKey($event, zoomed, i)"
        >
          <span
            v-for="name in HANDLE_NAMES"
            v-show="!cutLocked"
            :key="name"
            class="cs-handle"
            :class="`cs-handle--${name}`"
            aria-hidden="true"
            data-testid="cs-handle"
            @pointerdown="beginDrag($event, zoomed, i, HANDLES[name])"
          />
        </div>
      </template>
      <template #actions>
        <!-- How the boxes move, said once beside them rather than as a
             hover tip on a box being dragged; each box points to it. -->
        <span
          v-if="zoomed && !cutLocked"
          id="cs-cut-hint"
          class="cs-cap"
          data-testid="cs-cut-hint"
          >{{ CUT_HINT }}</span
        >
        <button
          v-if="zoomedEdited"
          type="button"
          class="cs-link"
          data-testid="cs-cut-reset"
          :disabled="cutLocked"
          @click="resetCut(zoomed.picture_id)"
        >
          Reset to automatic
        </button>
        <!-- Both choices always shown, so the control never changes size and
             says which one the sheet is on. -->
        <Segmented
          v-if="zoomed"
          :model-value="isKept(zoomed) ? 'keep' : 'drop'"
          :options="keepOptions(zoomed)"
          variant="icon-label"
          aria-label="This sheet"
          @update:model-value="setKept(zoomed.picture_id, $event === 'keep')"
        />
      </template>
    </ZoomPopout>

    <!-- Mounted only while open, as the Run popup's is: it loads the
         library's facets when it opens. -->
    <PicturePicker
      v-if="pickerOpen"
      open
      subtitle="for the reference"
      @close="closePicker"
      @pick="onPicked"
    />
  </div>
</template>

<script setup>
/**
 * Step 1 of "Create a training set…" when the set is drawn rather than picked.
 *
 * Draws 2-4 character sheets with a MOSS-Image, Krea 2, Qwen-Image, FLUX.2
 * Klein or MiniMax H3 text-to-image workflow (#2700; an ordinary
 * `POST /workflows/run`, so it queues and shows in Tasks like any run), ranks
 * them by how alike each sheet's own faces are, and cuts the one the user picks
 * into six pictures on the character. Where the library has a Qwen-Image-Edit,
 * FLUX.2 Klein or MiniMax H3 sheet workflow (a card with `reference_inputs`,
 * #2700),
 * the rail offers a second way to draw: from a reference picture plus the
 * words (#2679). The picture
 * goes in through the run's own `inputs`, the sheets are ranked by closeness
 * to it, and it stays in the rail beside them. Emits `done` with their ids, and the
 * dialog's picker takes over. The dialog owns the footer: it reads `footer`
 * and calls the actions this component exposes.
 *
 * On a training set's build screen (views/TrainingSetBuild.vue) the "dialog"
 * is that screen. The step also emits `cut` with the new pictures' ids as soon
 * as a sheet is cut, before `done`, and stores `snapshot` on the set, handed
 * back as `saved`, so it comes back as it was left: the sheets and their
 * ranking at once, the choices that name what it reads once it has read them.
 */
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { VIcon } from "vuetify/components";

import {
  buildSheetPrompt,
  rankItemSheets,
  rankSheets,
  useItemSheets,
  useSheets,
} from "../../api/characterSheets";
import {
  bulkAddPicturesToSet,
  createPictureSet,
  deletePictureSet,
} from "../../api/pictureSets";
import { listSetInStudio } from "../../api/studio";
import {
  getCharacter,
  getReferencePictures,
  patchCharacter,
} from "../../api/characters";
import { listAdapters } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import {
  getRunOutcome,
  getWorkflowCard,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  sheetCopyWorkflow,
} from "../../api/workflows";
import { useTasksStore } from "../../stores/useTasksStore";
import { proportionsFromDescription } from "../../utils/trainingSetBuild";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";
import { UNCHECKED_CODES } from "../../utils/runReasons";
import { buildMediaUrl } from "../../utils/media";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import { SAMPLER_CHOICES, SAMPLER_HINT } from "../../utils/samplerChoice";
import PicturePicker from "../widgets/PicturePicker.vue";
import RecentInput from "../widgets/RecentInput.vue";
import Segmented from "../widgets/Segmented.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";
import { useZoomPopout } from "../../composables/useZoomPopout.js";
import { usePromptAssist } from "../../composables/usePromptAssist";
import { readChoices, withRecent, writeChoices } from "../../utils/recentValues";
import { HANDLES, moveBox, nudgeBox, resizeBox } from "../../utils/sheetCuts";
import { ITEM_KINDS } from "../../utils/sheetKinds";
import PromptAssistGroup from "./PromptAssistGroup.vue";

const props = defineProps({
  /** What the sheets draw: a character's six photos, or four angles of an
   * outfit, a location or a prop (utils/sheetKinds.js). */
  kind: {
    type: String,
    default: "character",
    validator: (v) => v === "character" || v in ITEM_KINDS,
  },
  /** { id, name } of the character: whose sheets, or whose outfit. A
   * location or a prop has none. */
  character: { type: Object, default: null },
  /** How many pictures the character already has. */
  pictureCount: { type: Number, default: 0 },
  /** The step as `snapshot` last stored it, or null for a fresh one. */
  saved: { type: Object, default: null },
});

const emit = defineEmits(["cut", "done", "faces"]);

const FRAMINGS = [
  { id: "waist_up", label: "Waist-up" },
  { id: "mid_thigh", label: "Mid-thigh" },
  { id: "knees_up", label: "Knees-up" },
  { id: "full_body", label: "Full body" },
];
const COUNTS = [2, 3, 4].map((n) => ({ id: n, label: String(n) }));
// The canvas sizes `POST /character-sheets/prompt` draws at, and the passport
// photo each cuts to (a 600×768 cell of the large sheet, scaled).
const SIZES = [
  { id: "small", label: "Small", passport: "300×384", w: 1280, h: 768 },
  { id: "medium", label: "Medium", passport: "431×552", w: 1840, h: 1104 },
  { id: "large", label: "Large", passport: "600×768", w: 2560, h: 1536 },
  { id: "4k", label: "4K", passport: "900×1152", w: 3840, h: 2304 },
  { id: "5k", label: "5K", passport: "1237×1584", w: 5280, h: 3168 },
  { id: "6k", label: "6K", passport: "1462×1872", w: 6240, h: 3744 },
];
// The sizes a model draws well, and the one it starts at; the rest are greyed
// out. MOSS-Image, Krea 2, Qwen and Klein are known good to ~4 MP; MiniMax H3
// and H3 Image want a large canvas (an engine's own `sizes`).
const SIZES_DRAWN_WELL = ["small", "medium", "large"];
// The figure the sheet draws (#1079): the words alone lose to a layout made
// for a typical adult, so the layout changes with it.
const PROPORTIONS = [
  { id: "typical", label: "Typical" },
  { id: "little_person", label: "Little person" },
  { id: "cartoon", label: "Cartoon" },
];
// A prose prompt asks for unedited camera photos unless this is off.
const PHOTO_LOOKS = [
  { id: true, label: "Natural" },
  { id: false, label: "Off" },
];
const MOSS = "moss_image";
// Only MOSS-Image reads the layout JSON; every other engine's sheet is the
// prose prompt. Qwen-Image, FLUX.2 Klein and MiniMax H3 also draw from a
// reference picture (#2679, #2700). Each engine loads its own LoRAs.
// ponytail: no probe has measured Krea 2's, Qwen's, Klein's or H3's sheets yet.
const KREA = "krea2";
const QWEN = "qwen";
const KLEIN = /klein/i;
const ENGINES = {
  moss: {
    family: MOSS,
    name: "MOSS-Image",
    fits: (lora) => lora.base_model_family === MOSS,
  },
  krea: {
    family: KREA,
    name: "Krea 2",
    fits: (lora) => lora.base_model_family === KREA,
  },
  qwen: {
    family: QWEN,
    name: "Qwen-Image",
    fits: (lora) => lora.base_model_family === QWEN,
  },
  // A FLUX.2 dev LoRA does not load into Klein. ponytail: 4B and 9B Klein
  // LoRAs are not told apart; the shelf files both as FLUX.2 Klein.
  klein: {
    family: "flux2",
    name: "FLUX.2 Klein",
    fits: (lora) =>
      lora.base_model_family === "flux2" &&
      KLEIN.test(lora.base_model_canonical || lora.base_model || ""),
  },
  minimax: {
    family: "minimax_h3",
    name: "MiniMax H3",
    fits: (lora) => lora.base_model_family === "minimax_h3",
    sizes: ["large", "5k"],
    size: "5k",
  },
  // MiniMax's still-image model, another architecture than H3 video: an H3
  // video LoRA is no LoRA for it. A reference sheet at Large draws waxy
  // faces that drift from the picture; from 4K up they hold (2026-11-22).
  minimaxImage: {
    family: "minimax_h3_image",
    name: "MiniMax H3 Image",
    fits: (lora) => lora.base_model_family === "minimax_h3_image",
    sizes: ["4k", "5k", "6k"],
    size: "4k",
  },
};
const MODES = [
  { id: "text", label: "Text" },
  { id: "reference", label: "Reference and text" },
];
// Drawn from a picture, what the sheets are compared with: that picture, or
// the character's reference cluster, where each face counts its best match.
const COMPARE_WITH = [
  { id: "picture", label: "This picture" },
  { id: "cluster", label: "Reference cluster" },
];
const ORDINALS = ["first", "second", "third", "fourth"];
const POLL_MS = 2500;
// A ComfyUI that did not answer is asked again this often. The server keeps a
// failed read for a minute (`OBJECT_INFO_CACHE_TTL_S`), so asking sooner only
// hears the same failure.
const RECHECK_MS = 15_000;
// What the step says instead of blaming the workflows when ComfyUI never
// answered their check: nothing is known about any of them.
const COMFYUI_DOWN = {
  comfyui_unreachable:
    "ComfyUI isn't answering, so no sheet can be drawn. Start ComfyUI: the sheet workflows are checked again by themselves once it answers.",
  comfyui_not_configured:
    "PixlStash has no ComfyUI address to draw sheets with. Add one in Settings, then open this again.",
};
// Panels are filed before their faces are read; the picker ranks by face, so
// it waits for them, but not forever: a slow queue still hands over.
const FACE_WAIT_MS = 90_000;
// A sheet whose faces are still unread this long after its runs ended (no face
// model, a failed extraction, a deleted picture) stops being waited for: it
// can be chosen unscored, and the polling stops.
const SHEET_FACE_WAIT_MS = 120_000;
const HEIGHT_WORDS = /\b(tall|short|petite|height|legs?|leggy|long-legged)\b/i;
// The last choices made on this machine (`readChoices`), which a fresh step
// starts from: the mode, each mode's workflow, the size, the count, and each
// person's LoRA. The fields whose last values a fresh step starts from and
// offers as typed:
const RECENT_FIELDS = ["top", "trousers", "shoes", "background"];

const tasks = useTasksStore();
/** The item kind's words and fields, or null for a character's sheets. */
const item = computed(() => ITEM_KINDS[props.kind] || null);
const name = computed(() => props.character?.name || "");
/** What an item's panels are filed as: the set it names. */
const itemName = computed(
  () => form.name.trim() || item.value?.newName || "",
);
/** Where a mode's last workflow is remembered: an item kind's apart from a
 * character's, since items draw only in prose. */
function choiceKey(mode) {
  return item.value ? `${props.kind}:${mode}` : mode;
}
const phase = ref("describe"); // describe | drawing | pick | filing
const form = reactive({
  description: "",
  top: "",
  trousers: "",
  shoes: "",
  background: "",
  framing: "mid_thigh",
  size: "large",
  count: 4,
  proportions: "typical",
  // An older saved form has none, and reads as on.
  photo_look: true,
  // An item's name: the set its panels go in.
  name: "",
});
// A step with nothing saved starts from the choices last made.
const remembered = props.saved ? null : readChoices();
if (SIZES.some((s) => s.id === remembered?.size)) form.size = remembered.size;
// An item starts at two sheets, whatever a character's draw last asked for.
if (item.value) form.count = 2;
else if (COUNTS.some((c) => c.id === remembered?.count)) {
  form.count = remembered.count;
}
if (typeof remembered?.photo_look === "boolean") {
  form.photo_look = remembered.photo_look;
}
// Per field, the values sheets were drawn with, newest first; "" where the
// last draw left the field empty, so an empty field stays empty.
const recent = ref(readRecent(remembered || readChoices()));
if (!props.saved) {
  for (const field of RECENT_FIELDS) form[field] = recent.value[field][0] || "";
}
const offered = computed(() =>
  Object.fromEntries(
    RECENT_FIELDS.map((field) => [field, recent.value[field].filter(Boolean)]),
  ),
);
const framingTouched = ref(false);
const proportionsTouched = ref(false);
const savedDescription = ref("");
const loras = ref([]);
const loraSha = ref("");
const strength = ref(0.8);
const workflows = ref([]);
// Cards that read a picture while drawing on a canvas of their own, and the
// edit card one could be made from when there is none.
const referenceCards = ref([]);
const convertible = ref(null);
// Text-to-image cards a sheet copy can be made from (any model but MOSS's,
// which reads the layout itself), and the one picked to copy.
const textConvertibles = ref([]);
const textSourceId = ref("");
const textSource = computed(
  () =>
    textConvertibles.value.find((card) => card.id === textSourceId.value) ||
    textConvertibles.value[0] ||
    null,
);
const textConvertibleOptions = computed(() =>
  textConvertibles.value.map((card) => ({ value: card.id, label: card.name })),
);
const mode = ref("text"); // text | reference
// The picture the next sheets are drawn from, and the one the sheets on show
// were: the rail and the ranking follow the second.
const referenceId = ref(null);
const drawnFrom = ref(null);
const compareTo = ref("picture"); // picture | cluster
const pickerOpen = ref(false);
const pickButton = ref(null);
// How many faces the last ranking measured closeness against.
const rankedAgainst = ref(0);
const workflowsLoaded = ref(false);
/** The workflow list is not known yet: its select shows a placeholder. */
const workflowsPending = computed(() => !workflowsLoaded.value);
const workflowId = ref("");
// Every candidate workflow's pre-flight, by id: its width/height inputs, its
// picture inputs, and why it can't draw a sheet ("" when it can). Only the
// ones that can are offered.
const checks = ref({});
const checksDone = ref(false);
const referenceIds = ref([]);
const runs = ref([]); // { key, status, pictureIds, message }
const ranking = ref({}); // picture id -> RankedSheet
const order = ref([]); // sheet picture ids in the order they arrived
// The sheets to keep, in the order ticked. The best is ticked for the user
// until they tick or untick one themselves.
const kept = ref([]);
const keptTouched = ref(false);
// The photo boxes the person drew, per sheet: picture id to six entries in
// panel order, each `[left, top, right, bottom]` fractions of the sheet or null
// where that photo is still the cutter's. A re-rank never touches an entry, so
// an edited box is never overwritten; "Reset to automatic" removes the sheet's.
const cutEdits = ref({});
const mainPane = ref(null);
const working = ref(false);
const error = ref("");
// Drawing waits for the character's stored description, so a typed one never
// overwrites it.
const loaded = ref(false);
const gaveUpOnFaces = ref(false);
let timer = null;
let recheckTimer = null;
// Bumped by each `checkCards`: an answer from an earlier one is dropped.
let checkRun = 0;
let ticking = false;
let runsEndedAt = 0;
let alive = true;
let restoring = false;
// What the sheets on show were drawn with. A draw with anything else starts a
// new pick: the earlier sheets are of another prompt, not more of this one.
const drawnWith = ref("");
// The step has read its choices and restored `saved`: until then its
// snapshot would store the defaults over what was saved.
const restored = ref(false);
// What the owner picked by hand before then ("mode", "workflow", "lora",
// "strength", "reference", "size"): the step is usable from its card cache at
// once, and restoring, or a size defaulting, must not take a pick back.
const picked = new Set();
// The kept sheets were cut and their pictures filed; their faces are still
// being read.
const cutDone = ref(false);
// Every run this build started and every sheet it drew, never cleared: how
// Tasks finds the set a Character sheet row belongs to.
const runHistory = ref([]);
const drawnIds = ref([]);
// Runs still going when a new draw or a cut started over: no longer on show,
// but still the build's, so deleting the set stops them too.
const strayKeys = ref([]);

const fromReference = computed(() => mode.value === "reference");
/** The sheets are measured against the picture they were drawn from. */
const toPicture = computed(
  () => !!drawnFrom.value && compareTo.value === "picture",
);
const engine = computed(() => {
  if (!fromReference.value) {
    const card = workflows.value.find((c) => c.id === workflowId.value);
    if (card?.sheet_layout) {
      // A sheet copy draws the layout as regions on whatever model it runs.
      // Its diffusion model's, never a text encoder's, as referenceEngine.
      const model =
        (card.models || []).find((m) =>
          ["unet", "checkpoint"].includes(m.kind),
        ) || {};
      const family = model.base_model_family || "";
      return {
        family,
        name: model.base_model || "its model",
        fits: (lora) => !!family && lora.base_model_family === family,
        regions: true,
      };
    }
    return cardEngine(card) || ENGINES.moss;
  }
  const card =
    referenceCards.value.find((c) => c.id === workflowId.value) ||
    referenceCards.value[0];
  return referenceEngine(card) || ENGINES.qwen;
});
/** How the prompt route lays the sheet out: "" for MOSS-Image's layout JSON,
 * "regions" for a sheet copy, "prose" (one sentence) for every other engine
 * and for a draw from a reference. */
const layout = computed(() => {
  if (fromReference.value || item.value) return "prose";
  if (engine.value.regions) return "regions";
  return engine.value === ENGINES.moss ? "" : "prose";
});
/** The rail is the reference's: its control before drawing, the picture after. */
/** The Photo look a check asks for with the shipped choices, on: what a first
 * open shows. `SHEET_LOOK_OFF` is the same with the look turned off. */
const SHEET_LOOK_DEFAULT = { context: "sheet", on: true, choices: {} };
const SHEET_LOOK_OFF = { context: "sheet", on: false, choices: {} };
/** The prompt a look check is asked with. A draw always sends the sheet
 * prompt, and the server adds the look's lines (its `parts`) only to a prompt
 * with words, so a check without one would answer that nothing is added. The
 * sheet prompt itself is written only at the draw. */
const SHEET_PROMPT_STAND_IN = "A character sheet.";
/** The chosen workflow's Photo look for sheets, as its check answered (the
 * check asks for it with the workflow's own pre-flight), or null. */
const sheetAssist = computed(() =>
  layout.value === "prose" ? checks.value[workflowId.value]?.assist || null : null,
);
/** The Photo look is not known yet: the chosen workflow has not answered its
 * check (or, with none chosen, the list is not checked). Only then is its
 * place held, so a workflow that never answers does not hold it for good. A
 * chosen workflow holds it only on the prose layout, the one that can show a
 * Photo look; with none chosen, only while a listed workflow could show one. */
const sheetLookPending = computed(() =>
  workflowId.value
    ? layout.value === "prose" && !checks.value[workflowId.value]
    : !workflowsLoaded.value &&
      (!modeCards.value.length || modeCards.value.some(drawsProse)),
);
/** Whether *card* draws its sheet in prose, the layout a Photo look rides on:
 * every draw from a reference, and any text one but MOSS-Image's and a sheet
 * copy's regions. */
function drawsProse(card) {
  return (
    fromReference.value ||
    !!item.value ||
    (!card.sheet_layout && cardEngine(card) !== ENGINES.moss)
  );
}
const sheetLook = usePromptAssist(sheetAssist, "sheet");
/** Whether the chosen workflow offers the sampler choice (`prompt.sampler_choice`). */
const sheetSamplerChoice = computed(
  () => layout.value === "prose" && checks.value[workflowId.value]?.samplerChoice === true,
);
/** The sampler of the draw: LCM as saved, or Euler on the run's copy. Per draw. */
const sampler = ref("lcm");
const samplerOptions = computed(() =>
  SAMPLER_CHOICES.map((o) => ({ ...o, testid: `cs-sampler-${o.id}` })),
);
let sheetAssistAsk = 0;
/** Put a workflow's answer about its look into its check, where the step reads it. */
function setLookAnswer(id, answer) {
  checks.value = { ...checks.value, [id]: { ...checks.value[id], ...answer } };
}
/**
 * Ask the chosen workflow for the lines the owner's Photo look adds. Only the
 * owner's own choices need this: the check already answered the shipped ones.
 */
async function askSheetAssist() {
  const ask = ++sheetAssistAsk;
  const id = workflowId.value;
  if (!id || layout.value !== "prose") return;
  try {
    const answer = await preflightWorkflowRun({
      workflow_id: id,
      count: 1,
      prompt: SHEET_PROMPT_STAND_IN,
      prompt_assist: sheetLook.request() || SHEET_LOOK_OFF,
    });
    if (alive && ask === sheetAssistAsk) {
      const prompt = answer?.groups?.[0]?.prompt;
      setLookAnswer(id, {
        assist: prompt?.assist || null,
        samplerChoice: prompt?.sampler_choice === true,
      });
    }
  } catch (e) {
    console.warn("Couldn't ask for the photo look of sheet workflow", id, e);
    if (alive && ask === sheetAssistAsk) setLookAnswer(id, { assist: null, samplerChoice: false });
  }
}
// Another workflow or layout: the draw's sampler starts again. The look is the
// check's answer for that workflow, so it arrives with the workflow's own
// check; a remembered choice of the owner's is asked for once more.
watch([workflowId, layout], () => {
  sampler.value = "lcm";
  if (sheetLook.changed.value) askSheetAssist();
});
// #2776's remembered Off seeds the group's Off, once.
watch(sheetAssist, (assist) => {
  if (assist && !form.photo_look && !sheetLook.hasStoredOn()) sheetLook.setOn(false);
});
// And back: the group stands in for the Natural/Off switch, so an engine
// without the group (no presets) keeps the last On/Off the owner chose.
watch(
  () => (sheetAssist.value ? sheetLook.on.value : null),
  (on) => {
    if (on !== null && form.photo_look !== on) form.photo_look = on;
  },
);

const railIsReference = computed(() =>
  phase.value === "describe" ? fromReference.value : !!drawnFrom.value,
);

const chosenLora = computed(
  () => loras.value.find((lora) => lora.sha256 === loraSha.value) || null,
);
const trigger = computed(() => {
  const words = chosenLora.value?.trigger_words;
  return (Array.isArray(words) ? words[0] : words) || "";
});
const loraName = computed(() =>
  chosenLora.value
    ? chosenLora.value.display_name || chosenLora.value.filename
    : "",
);

const loraOptions = computed(() => [
  { value: "", label: "No LoRA" },
  ...loras.value.map((lora) => {
    const fits = engine.value.fits(lora);
    return {
      value: lora.sha256,
      label: lora.display_name || lora.filename,
      disabled: !fits,
      chips: [
        fits
          ? lora.base_model || engine.value.name
          : `For ${lora.base_model || "another model"}; sheets run ${engine.value.name}`,
      ],
    };
  }),
]);

const modeCards = computed(() =>
  fromReference.value ? referenceCards.value : workflows.value,
);

/** The mode's workflows that pass their pre-flight: the only ones offered. */
const usableCards = computed(() =>
  modeCards.value.filter((card) => checks.value[card.id]?.problem === ""),
);

const workflowOptions = computed(() =>
  usableCards.value.map((card) => ({ value: card.id, label: card.name })),
);

// The chosen workflow's sizes and picture inputs, as its pre-flight named them.
const workflowSizes = computed(() => checks.value[workflowId.value]?.sizes || []);
const workflowInputs = computed(
  () => checks.value[workflowId.value]?.inputs || [],
);

/** Why no sheet can be drawn with the workflows there are ("" when one can,
 * null while they are still being checked, and while there are none: the
 * no-workflow notice says that). */
const workflowProblem = computed(() => {
  // The chosen one passing is enough to draw: the rest (a hundred workflows
  // in a large library) may still be checking.
  if (checks.value[workflowId.value]?.problem === "") return "";
  if (!checksDone.value || !modeCards.value.length) return null;
  if (usableCards.value.length) return workflowId.value ? "" : null;
  const down = modeCards.value
    .map((card) => checks.value[card.id])
    .find((check) => check?.down);
  if (down) return down.problem;
  const first = checks.value[modeCards.value[0].id]?.problem || "";
  return modeCards.value.length === 1
    ? first
    : `None of the ${modeCards.value.length} sheet workflows can draw a sheet. ${first}`;
});

/** The reference card a sheet copy would make usable, while none can draw. */
const sizeless = computed(() =>
  fromReference.value && !usableCards.value.length
    ? referenceCards.value.find((card) => checks.value[card.id]?.sizeless) ||
      null
    : null,
);

/** The newest LoRA that loads into the mode's sheet workflow. */
function fittingLora() {
  return (
    loras.value.find((l) => engine.value.fits(l))?.sha256 || ""
  );
}

// Each mode has its own workflows: the one last chosen in it, or its first.
watch(mode, (now) => {
  const last = readChoices().workflows?.[choiceKey(now)];
  workflowId.value = usableCards.value.some((card) => card.id === last)
    ? last
    : usableCards.value[0]?.id || "";
});

// Each model has its own LoRAs, and a mode can hold two (MOSS-Image and
// Krea 2; Qwen and FLUX.2 Klein): a LoRA follows the chosen workflow's model.
watch(engine, () => {
  if (!chosenLora.value || !engine.value.fits(chosenLora.value)) {
    loraSha.value = fittingLora();
  }
});

// Each model its own sizes, so the size follows the chosen workflow's model
// too: a model with a size of its own (MiniMax H3) starts there, any other
// keeps a size it draws well. Only once restored, and a size saved with the
// set, or last picked for this model, stands over the one it would start at.
const sizesDrawnWell = computed(() => engine.value.sizes || SIZES_DRAWN_WELL);
const sizeOptions = computed(() =>
  SIZES.map((s) => ({ ...s, disabled: !sizesDrawnWell.value.includes(s.id) })),
);
watch([engine, restored], ([now, done], [, wasDone]) => {
  if (!done) return;
  const saved =
    !wasDone &&
    (picked.has("size") ||
      !!(props.saved?.form?.size || remembered?.size_family === now.family));
  const keep =
    sizesDrawnWell.value.includes(form.size) && (!now.size || saved);
  if (!keep) form.size = now.size || "large";
});

const referenceHint = computed(() => {
  const faces = referenceIds.value.length
    ? `${name.value}'s reference faces. `
    : "";
  return `${faces}The sheet workflow takes one picture; the dashed tile opens the picker.`;
});

function referenceWords(id) {
  const index = referenceIds.value.indexOf(id);
  return index < 0
    ? "A picture you picked"
    : `${name.value}'s ${ORDINALS[index]} reference face`;
}

/** The picker opens programmatically, so Vuetify has no activator to hand
 * focus back to: the pick button takes it. */
function closePicker() {
  pickerOpen.value = false;
  nextTick(() => pickButton.value?.focus());
}

function onPicked(picture) {
  closePicker();
  if (picture?.id) {
    picked.add("reference");
    referenceId.value = picture.id;
  }
}

const suggestedFraming = computed(() =>
  chosenLora.value ? "full_body" : "mid_thigh",
);

watch(suggestedFraming, (framing) => {
  if (!framingTouched.value) form.framing = framing;
});

// "Adult woman with dwarfism" draws a little person: the description decides
// until a toggle is picked by hand. Who is drawn is the description's alone:
// the sheet names "the person".
watch(
  () => form.description,
  (text) => {
    const proportions = proportionsFromDescription(text);
    if (proportions && !proportionsTouched.value) form.proportions = proportions;
  },
);


const framingHint = computed(() => {
  const reason = chosenLora.value
    ? "Suggested: full body. The LoRA holds the face, so the photos can show the whole body."
    : "Suggested: mid-thigh, the framing that held its layout best. With no LoRA the sheet alone carries the face; waist-up gives the faces the most pixels.";
  const height =
    form.framing !== "full_body" && HEIGHT_WORDS.test(form.description)
      ? " This framing won't show height."
      : "";
  return reason + height;
});

const sizeHint = computed(() => {
  // An item has no size to pick.
  if (item.value) return "";
  const size = SIZES.find((s) => s.id === form.size);
  const own = engine.value.sizes;
  const labels = (own || []).map((id) => SIZES.find((s) => s.id === id).label);
  const why = own
    ? ` ${engine.value.name} needs ${labels.slice(0, -1).join(", ")} or ${labels.at(-1)}.`
    : " 4K and 6K are for MiniMax H3 Image, 5K for both MiniMax models.";
  return `Passport photos come out ${size.passport}. Larger is slower to draw.${why}`;
});

const descriptionHint = computed(() => {
  if (item.value) {
    return fromReference.value
      ? `The picture shows the ${item.value.noun}; these words are sent with it.`
      : item.value.descriptionHint;
  }
  if (fromReference.value) {
    const from = savedDescription.value ? `From ${name.value}. ` : "";
    return `${from}In this mode the picture sets the face; these words are sent with it.`;
  }
  // The build used to be a field of its own; it belongs in the words now.
  const build = " Say the build too: it is in every photo, and a different build draws a different face.";
  if (savedDescription.value) {
    return `From ${name.value}. Changes here apply to these sheets only.${build}`;
  }
  return `${name.value} has no description yet. This one is saved as theirs when the first sheets are drawn.${build}`;
});

const likenessNotice = computed(() => {
  if (chosenLora.value) {
    return `Drawn with ${loraName.value}, a LoRA of ${name.value}, so the sheets start from that likeness. Without a LoRA they would be a new likeness drawn from words.`;
  }
  if (props.pictureCount > 0) {
    return `These sheets are drawn from words, not from ${name.value}'s ${props.pictureCount} pictures. What they make is a new likeness, filed on ${name.value} and ranked beside the pictures already there.`;
  }
  return "";
});

const ended = computed(() =>
  order.value.filter((id) => ranking.value[id]?.faces_read),
);

function score(sheet) {
  return (sheet.agreement ?? -1) + (sheet.closeness ?? 0);
}

/** Drawn from a picture, closeness to it is what matters most. */
function better(a, b) {
  if (!drawnFrom.value) return score(b) - score(a);
  return (
    (b.closeness ?? -1) - (a.closeness ?? -1) ||
    (b.agreement ?? -1) - (a.agreement ?? -1)
  );
}

/** Every sheet, best first; broken ones last, then those that won't cut
 * cleanly. */
const sheets = computed(() => {
  const scored = order.value.map((id) => ({
    picture_id: id,
    faces_read: false,
    ...(ranking.value[id] || {}),
  }));
  return scored.sort(
    (a, b) =>
      Number(!!a.broken) - Number(!!b.broken) ||
      Number(a.cuts_cleanly === false) - Number(b.cuts_cleanly === false) ||
      Number(b.faces_read) - Number(a.faces_read) ||
      better(a, b),
  );
});

const holding = computed(() =>
  sheets.value.filter((s) =>
    item.value
      ? s.readable && (s.panels || []).every((p) => p.agrees)
      : s.faces_read &&
        !s.broken &&
        s.agreement_words !== "faces don't hold",
  ),
);

const runsEnded = computed(() =>
  runs.value.every((run) => ["completed", "failed"].includes(run.status)),
);

const allEnded = computed(
  () =>
    runsEnded.value &&
    (gaveUpOnFaces.value ||
      order.value.every((id) => ranking.value[id]?.faces_read)),
);

const noneHold = computed(
  () => allEnded.value && ended.value.length > 0 && !holding.value.length,
);

/** Drawn from a picture, only a sheet close to it is kept for the user: why
 * none was, or "". */
const noneClose = computed(() => {
  if (item.value) return "";
  if (!drawnFrom.value || !allEnded.value || !holding.value.length) return "";
  if (holding.value.some((sheet) => sheet.close)) return "";
  if (!rankedAgainst.value) {
    if (!toPicture.value) return "";
    return "No face was read in the reference picture, so the sheets can't be compared with it and none is kept for you. A reference with the face to the camera works best.";
  }
  const n = ended.value.length;
  if (!toPicture.value) {
    return `None of the ${n} sheets came close to ${name.value}'s reference cluster, so none is kept for you. Drawing more usually finds one.`;
  }
  return `None of the ${n} sheets came close to the reference, so none is kept for you. Drawing more usually finds one. A different reference picture, face to the camera, helps too.`;
});

const failures = computed(() =>
  runs.value
    .filter((run) => run.status === "failed" && !run.stopped)
    .map((run) => run.message || "the run failed."),
);

function runEnded(run) {
  return ["completed", "failed"].includes(run.status);
}

/** What each run shows, in the order the runs were started: as many places as
 * it drew sheets, or one while it draws. A sheet lands in its run's place and
 * nothing moves as the others arrive; its number is its place. */
const slots = computed(() => {
  const byId = new Map(sheets.value.map((sheet) => [sheet.picture_id, sheet]));
  const live = new Map(tasks.comfyuiRuns.map((run) => [runKey(run), run]));
  const out = [];
  for (const run of runs.value) {
    const drawn = (run.pictureIds || []).filter((id) => byId.has(id));
    for (const id of drawn) {
      out.push({ key: `sheet-${id}`, sheet: byId.get(id) });
    }
    if (drawn.length) continue;
    const progress = live.get(run.key);
    // ComfyUI drawing it; done there and being imported (or gone from the
    // runs in flight once seen, until its outcome is read); not started.
    const generating =
      progress?.status === "running" && !progress.generated;
    const displaying =
      progress?.status === "running" ||
      (!progress && seenLive.value.has(run.key));
    out.push({
      key: run.key,
      run,
      label: run.stopped
        ? "stopped"
        : runEnded(run)
          ? "didn't draw"
          : run.stopping
            ? "stopping"
            : generating
              ? "generating"
              : displaying
                ? "displaying"
                : "queued",
      percent: generating ? Math.round(Number(progress.percent) || 0) : null,
      // Done drawing and being imported, there is nothing left to stop; while
      // it draws (`displaying` holds then too) it can still be stopped.
      stoppable:
        !runEnded(run) && !run.stopping && (generating || !displaying),
    });
  }
  return out.map((slot, index) => ({ ...slot, n: index + 1 }));
});

// The runs the Tasks store has listed as in flight at least once: one gone
// from that list since has finished on ComfyUI, not still waiting to start.
const seenLive = ref(new Set());
watch(
  [() => tasks.comfyuiRuns, () => runs.value.length],
  ([live]) => {
    const listed = new Set(live.map((run) => runKey(run)));
    const seen = runs.value.filter(
      (run) => listed.has(run.key) && !seenLive.value.has(run.key),
    );
    if (seen.length) {
      seenLive.value = new Set([...seenLive.value, ...seen.map((r) => r.key)]);
    }
  },
  { immediate: true },
);

const sheetNumber = computed(
  () =>
    new Map(
      slots.value
        .filter((slot) => slot.sheet)
        .map((slot) => [slot.sheet.picture_id, slot.n]),
    ),
);

const waitingRuns = computed(() => runs.value.filter((run) => !runEnded(run)));

/** A sheet with its number: its place among the slots. */
function numbered(id) {
  const sheet = sheets.value.find((s) => s.picture_id === id);
  return sheet ? { ...sheet, n: sheetNumber.value.get(id) } : null;
}

const keptSheets = computed(() =>
  kept.value.map(numbered).filter(Boolean),
);

function isKept(sheet) {
  return kept.value.includes(sheet.picture_id);
}

function setKept(id, on) {
  if (isKept({ picture_id: id }) !== on) toggleKeep(id);
}

function keepOptions(sheet) {
  const disabled = sheetLocked(sheet);
  return [
    { id: "keep", label: "Keep", icon: "check", disabled },
    { id: "drop", label: "Drop", icon: "delete-outline", disabled },
  ];
}

function toggleKeep(id) {
  keptTouched.value = true;
  kept.value = kept.value.includes(id)
    ? kept.value.filter((k) => k !== id)
    : [...kept.value, id];
}

const pickIntro = computed(() => {
  if (item.value) {
    const views = item.value.views === "views" ? "view" : "angle";
    const angles = item.value.angles;
    return `Each ${views}'s number is how well its colours agree with the other three. Keep the sheet to use: its four panels become ${itemName.value}'s ${angles.slice(0, -1).join(", ")} and ${angles.at(-1)}, in a set of their own. Keeping more than one sheet keeps more than one version. Every sheet stays in the Scrapheap, used or not.`;
  }
  if (drawnFrom.value) {
    // Sheets keep their run's place, so the closest is kept, not moved first.
    const against = toPicture.value
      ? "the picture on the right"
      : `${name.value}'s reference cluster, each face by its best match there`;
    return `Each sheet says how close it comes to ${against}, and each photo on it says the same for itself. The closest is kept for you if it comes close enough. Keep the ones to use: the six photos of each become pictures of ${name.value}. Every sheet stays in the Scrapheap, used or not.`;
  }
  const base = `Each sheet says how well its own faces agree. Keep the ones to use: the six photos of each become pictures of ${name.value}. Every sheet stays in the Scrapheap, used or not, where you can restore any you like.`;
  if (!referenceIds.value.length) {
    return `${base} Each photo's number is how well its face agrees with the other five.`;
  }
  return `${base} Each is also compared with ${name.value}'s reference faces, and each photo's number is how close it comes to them: a sheet that agrees with itself but drifts from them is a different person drawn well.`;
});

/**
 * Each photo's own likeness, to show under its face: to the reference faces
 * when the ranking had any, else to the sheet's other photos. A photo that
 * drifts is marked.
 */
function photoScores(sheet) {
  const toReferences = sheet.closeness != null;
  return (sheet.panels || []).flatMap((photo) => {
    const value = toReferences ? photo.closeness : photo.agreement;
    if (value == null) return [];
    const [left, , right, bottom] = photo.box;
    return [
      {
        panel: photo.panel,
        value,
        ok: toReferences ? photo.close : photo.agrees,
        style: {
          left: `${((left + right) / 2) * 100}%`,
          top: `min(${bottom * 100}%, calc(100% - 22px))`,
        },
      },
    ];
  });
}

/** The photos that drift, for the sheet's label: "" when none does. */
function photoWords(sheet) {
  // An item sheet's words already name the panel that drifts.
  if (item.value) return "";
  const off = photoScores(sheet)
    .filter((photo) => !photo.ok)
    .map((photo) => photo.panel + 1);
  if (!off.length) return "";
  return off.length === 1
    ? ` · photo ${off[0]} drifts`
    : ` · photos ${off.join(", ")} drift`;
}

function sheetWords(sheet) {
  if (!sheet.faces_read) {
    return gaveUpOnFaces.value ? "faces not read" : "reading faces…";
  }
  if (sheet.broken) return "more than one person in a photo";
  const words = [sheet.agreement_words];
  if (sheet.close != null) {
    const near = closeWords(sheet);
    // Drawn from a picture, closeness leads, with its number.
    if (drawnFrom.value) words.unshift(`${sheet.closeness.toFixed(2)} ${near}`);
    else words.push(near);
  }
  // A warning, not a refusal: the zoom shows where it cuts. A cut the person
  // drew is theirs, so it is no longer the cutter's to doubt.
  if (sheetEdited(sheet)) words.push("cut edited");
  else if (sheet.cuts_cleanly === false) words.push("may not cut cleanly");
  return words.join(" · ");
}

/** A cell, `[left, top, right, bottom]` fractions, placed on the zoomed sheet. */
function cellStyle([left, top, right, bottom]) {
  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${(right - left) * 100}%`,
    height: `${(bottom - top) * 100}%`,
  };
}

/** The photos a sheet is cut into now: each the person's box where they drew
 * one, else the cutter's latest. */
function cellsOf(sheet) {
  const edits = cutEdits.value[sheet.picture_id] || [];
  return (sheet.cells ?? []).map((cell, i) => edits[i] ?? cell);
}

function boxEdited(sheet, index) {
  return !!cutEdits.value[sheet.picture_id]?.[index];
}

function sheetEdited(sheet) {
  return (cutEdits.value[sheet.picture_id] || []).some(Boolean);
}

/** Write one photo's box. A new object each time, so the change is reactive. */
function setBox(sheet, index, box) {
  const edits = [
    ...(cutEdits.value[sheet.picture_id] ||
      Array(PANEL_COUNT.value).fill(null)),
  ];
  edits[index] = box;
  cutEdits.value = { ...cutEdits.value, [sheet.picture_id]: edits };
}

/** Back to the cutter's latest boxes for a sheet. */
function resetCut(id) {
  const rest = { ...cutEdits.value };
  delete rest[id];
  cutEdits.value = rest;
}

/** Closeness in words: to the picture drawn from, else to the character. */
function closeWords(sheet) {
  const whom = toPicture.value ? "the reference" : name.value;
  return sheet.close ? `close to ${whom}` : `drifts from ${whom}`;
}

function meterWidth(sheet) {
  return `${Math.round(Math.min(1, Math.max(0, sheet.closeness)) * 100)}%`;
}

function sheetLocked(sheet) {
  return (
    (!sheet.faces_read && !gaveUpOnFaces.value) ||
    working.value ||
    phase.value === "filing"
  );
}

const zoomed = computed(() =>
  phase.value === "describe" ? null : numbered(zoomedId.value),
);
const zoomedCut = computed(() => (zoomed.value ? cellsOf(zoomed.value) : []));
const zoomedEdited = computed(() => !!zoomed.value && sheetEdited(zoomed.value));
// Boxes are drawn but not moved while the sheets are being cut.
const cutLocked = computed(() => working.value || phase.value === "filing");
const HANDLE_NAMES = Object.keys(HANDLES);
const PANEL_COUNT = computed(() => (item.value ? 4 : 6));
const CUT_HINT =
  "Drag a box's edge or corner to resize it, or its middle to move it. " +
  "Arrows move it; Shift or Alt with an arrow pushes or pulls that edge.";

/** The photo a box is, for its label: the passports 1-4, then the bodies. */
function panelName(index) {
  if (item.value) return item.value.angles[index];
  return index < 4 ? `Passport photo ${index + 1}` : `Body photo ${index - 3}`;
}

// The box a pointer is dragging: its sheet, the photo's index, the edges it
// moves (null for the whole box), where the drag began and the sheet's size on
// screen. Only one at a time.
let drag = null;

/** A drag on a photo's box (`edges` null) or one of its handles. The zoom's
 * pan must not take it, so the event stops here. */
function beginDrag(event, sheet, index, edges) {
  // A second finger on a touch screen is not a new drag: only the first moves.
  if (cutLocked.value || event.button !== 0 || event.isPrimary === false) return;
  event.stopPropagation();
  const box = event.currentTarget.closest(".cs-cell");
  const overlay = box?.closest(".zp-over");
  if (!overlay) return;
  box.setPointerCapture?.(event.pointerId);
  drag = {
    sheet,
    index,
    edges,
    start: cellsOf(sheet)[index],
    x: event.clientX,
    y: event.clientY,
    rect: overlay.getBoundingClientRect(),
  };
}

function moveDrag(event) {
  if (!drag) return;
  const dx = (event.clientX - drag.x) / drag.rect.width;
  const dy = (event.clientY - drag.y) / drag.rect.height;
  const box = drag.edges
    ? resizeBox(drag.start, drag.edges, dx, dy)
    : moveBox(drag.start, dx, dy);
  setBox(drag.sheet, drag.index, box);
}

function endDrag() {
  drag = null;
}

/** An arrow moves the focused photo's box, or with Shift or Alt one of its
 * edges. Only an arrow is taken: the zoom reads the arrows too, to step to the
 * next sheet. */
function onCellKey(event, sheet, index) {
  if (cutLocked.value) return;
  const box = cellsOf(sheet)[index];
  const next = nudgeBox(box, event.key, {
    shift: event.shiftKey,
    alt: event.altKey,
  });
  if (!next) return;
  event.preventDefault();
  event.stopPropagation();
  setBox(sheet, index, next);
}

/** The sheet's own file, full size; the thumbnail until its format is known. */
function sheetUrl(sheet) {
  if (!sheet.format) return pictureThumbnailUrl(sheet.picture_id);
  return appendShareToken(
    buildMediaUrl({ backendUrl: API_BASE_URL, image: { id: sheet.picture_id, format: sheet.format } }),
  );
}

/** The workflow list and its checks as the last full check left them, on this
 * machine: shown at once on the next open, then checked again behind it. */
const CARD_CACHE_KEY = "pixlstash:sheetWorkflowCards:v1";
function readCardCache() {
  try {
    return JSON.parse(localStorage.getItem(CARD_CACHE_KEY) || "null");
  } catch (e) {
    console.warn("Couldn't read the sheet workflows remembered here", e);
    return null;
  }
}
function writeCardCache(value) {
  try {
    localStorage.setItem(CARD_CACHE_KEY, JSON.stringify(value));
  } catch (e) {
    console.warn("Couldn't remember the sheet workflows", e);
  }
}

/**
 * Pre-flight one card: a sheet is drawn at a size of its own, so the workflow
 * must have one to set, and it must be able to run at all; from a reference it
 * also needs a picture input and a prompt PixlStash can write.
 */
async function checkCard(card, reference) {
  try {
    const [{ card: full }, check] = await Promise.all([
      getWorkflowCard(card.id),
      // The Photo look rides in the same pre-flight: its catalogue and the
      // sampler offer are what the step shows beside the workflow's list.
      preflightWorkflowRun(
        {
          workflow_id: card.id,
          count: 1,
          prompt: SHEET_PROMPT_STAND_IN,
          prompt_assist: SHEET_LOOK_DEFAULT,
        },
        { cached: true },
      ),
    ]);
    const name = full?.name || card.name || "This workflow";
    const sizes = (full?.defaults || []).filter((d) =>
      ["width", "height"].includes(d.input_name),
    );
    const groups = check?.groups || [];
    const prompt = groups[0]?.prompt;
    // ComfyUI never answered, so nothing is known about this workflow (nor
    // any other): that is what to say, not the workflow's own shortcomings.
    const down = comfyuiDown(groups);
    // The draw fills the picture inputs itself; a pinned one keeps its pin.
    const inputs = reference
      ? groups.flatMap((g) => g.picture_inputs || []).filter((i) => i.mode !== "fixed")
      : [];
    const refused = groups.some((g) =>
      (g.reasons || []).some(
        (r) => !(reference && r.code === "picture_input_unfilled"),
      ),
    );
    const problem = down
      ? COMFYUI_DOWN[down]
      : !sizes.length
        ? `${name} has no picture size to set, so it can't draw a sheet.`
        : refused
          ? `${name} can't run as it is. Open it from Workflows to see why.`
          : reference && !inputs.length
            ? `${name} has no picture input to put the reference in.`
            : reference && !groups.some((g) => g.prompt?.positive_settable)
              ? `${name} has no prompt PixlStash can write the sheet into.`
              : "";
    // All an edit workflow drawing at its picture's size lacks: a sheet copy
    // fixes that (#2700).
    const sizeless = reference && !sizes.length && !refused;
    return {
      sizes,
      inputs,
      problem,
      sizeless,
      down,
      assist: prompt?.assist || null,
      samplerChoice: prompt?.sampler_choice === true,
    };
  } catch (e) {
    console.warn("Couldn't check the sheet workflow", card.id, e);
    return {
      sizes: [],
      inputs: [],
      problem: `Couldn't check ${card.name || "this workflow"}: ${errorMessage(e)}`,
      // Never answered, so not remembered: the next open checks it again.
      failed: true,
      assist: null,
      samplerChoice: false,
    };
  }
}

/** The reason code saying ComfyUI never answered a pre-flight, or "". */
function comfyuiDown(groups) {
  const reasons = (groups || []).flatMap((g) => g.reasons || []);
  return reasons.find((r) => UNCHECKED_CODES.includes(r.code))?.code || "";
}

/** The mode's first card that passed, once every card before it has
 * answered: the one the whole check would pick, as soon as it is known. */
function firstUsable() {
  for (const card of modeCards.value) {
    const check = checks.value[card.id];
    if (!check) return "";
    if (check.problem === "") return card.id;
  }
  return "";
}

/** Pre-flight every candidate card of both modes, then keep the chosen one
 * only while it passes. Each answer lands as it comes, so the mode on show
 * (checked first) offers its workflows before the other mode is done. */
async function checkCards() {
  clearTimeout(recheckTimer);
  const run = ++checkRun;
  const first = fromReference.value;
  const all = [
    ...workflows.value.map((card) => [card, false]),
    ...referenceCards.value.map((card) => [card, true]),
  ].sort(([, a], [, b]) => Number(b === first) - Number(a === first));
  const results = await Promise.all(
    all.map(async ([card, ref]) => {
      const askAt = sheetAssistAsk;
      const result = await checkCard(card, ref);
      if (alive && run === checkRun) {
        // The check asked with the shipped look. Had the owner asked for their
        // own, while it was out or before (the re-ask at open for a remembered
        // look), their answer stands for the chosen workflow.
        const own =
          card.id === workflowId.value &&
          (sheetAssistAsk !== askAt || sheetLook.changed.value);
        const held = checks.value[card.id];
        checks.value = {
          ...checks.value,
          [card.id]:
            own && held
              ? { ...result, assist: held.assist, samplerChoice: held.samplerChoice }
              : result,
        };
        if (!workflowId.value) workflowId.value = firstUsable();
      }
      return result;
    }),
  );
  if (!alive || run !== checkRun) return;
  checksDone.value = true;
  if (!usableCards.value.some((card) => card.id === workflowId.value)) {
    workflowId.value = usableCards.value[0]?.id || "";
  }
  // An outage, or a check that threw, is not remembered: the next open checks
  // again, as this one did.
  if (!results.some((result) => result.down || result.failed)) {
    const faces = readCardCache()?.faces || {};
    writeCardCache({
      cards: cardRows,
      // This run's answers only: `checks` also holds what `askSheetAssist`
      // wrote for the owner's own look (or a failed ask's null), which is not
      // what the workflow's check answered.
      checks: Object.fromEntries(all.map(([card], i) => [card.id, results[i]])),
      // Whether this character has faces to draw from: the mode rule's input.
      ...(props.character
        ? { faces: { ...faces, [props.character.id]: referenceIds.value.length > 0 } }
        : { faces }),
    });
  }
  // Not configured is fixed in Settings and said so; only a ComfyUI that may
  // yet start is waited for.
  if (results.some((result) => result.down === "comfyui_unreachable")) {
    recheckWhenComfyuiAnswers(all[0][0]);
  }
}

/** Ask after one card until ComfyUI answers, then check them all again. */
function recheckWhenComfyuiAnswers(card) {
  const run = checkRun;
  recheckTimer = setTimeout(async () => {
    try {
      const check = await preflightWorkflowRun(
        { workflow_id: card.id, count: 1 },
        { cached: true },
      );
      if (!alive || run !== checkRun) return;
      // Answered, or now a failure Settings fixes: check afresh, which says
      // so and waits no longer.
      if (comfyuiDown(check?.groups) !== "comfyui_unreachable") {
        await checkCards();
        return;
      }
    } catch (e) {
      console.warn("Couldn't ask whether ComfyUI answers yet", card.id, e);
      if (!alive || run !== checkRun) return;
    }
    recheckWhenComfyuiAnswers(card);
  }, RECHECK_MS);
}

// The pop-out steps through the sheets on show, in their places.
const zoomList = computed(() =>
  slots.value.filter((slot) => slot.sheet).map((slot) => slot.sheet.picture_id),
);
// The sheet in the zoom pop-out, and the wheel that opened it, if one did.
// Closing goes back to the pick, focus on the sheet's own zoom control.
const {
  zoomedId,
  zoomEntry,
  hasPrev,
  hasNext,
  openZoom,
  onTileWheel,
  stepZoom,
  closeZoom,
} = useZoomPopout(zoomList, (id) =>
  mainPane.value
    ?.querySelector(`[data-testid="cs-zoom-open"][data-sheet="${id}"]`)
    ?.focus(),
);

watch(phase, () => {
  zoomedId.value = null;
});

function stopPolling() {
  if (timer) clearTimeout(timer);
  timer = null;
}

function schedule() {
  // A tick in flight schedules the next one itself when it ends.
  if (ticking || timer || !alive) return;
  timer = setTimeout(tick, POLL_MS);
}

async function followRuns() {
  for (const run of runs.value) {
    if (["completed", "failed"].includes(run.status)) continue;
    try {
      const outcome = await getRunOutcome(run.key);
      // A new prompt's draw dropped this run while it was being read.
      if (!runs.value.includes(run)) continue;
      run.status = outcome.status;
      run.message = outcome.message || "";
      run.stopped = !!outcome.stopped;
      run.pictureIds = outcome.picture_ids || [];
      for (const id of outcome.picture_ids || []) {
        if (!order.value.includes(id)) order.value.push(id);
        if (!drawnIds.value.includes(id)) drawnIds.value.push(id);
      }
    } catch (e) {
      if (e?.response?.status === 404) {
        run.status = "failed";
        run.message = "PixlStash lost track of this run. Check Tasks for it.";
      } else {
        console.warn("Couldn't read a sheet run", run.key, e);
      }
    }
  }
}

// Bumped by each ranking: an answer from an earlier one is dropped, so the
// scores on show are always against what the sheets are compared with now.
let rankRun = 0;

async function rank() {
  const run = ++rankRun;
  if (item.value) {
    const answer = await rankItemSheets(props.kind, order.value);
    if (run !== rankRun) return;
    // An item sheet has no faces to wait for: it is ready once ranked, so
    // it counts as read wherever a character sheet waits for its faces.
    ranking.value = Object.fromEntries(
      answer.sheets.map((sheet) => [
        sheet.picture_id,
        { ...sheet, faces_read: true },
      ]),
    );
    return;
  }
  // Drawn from a picture, the sheets are measured against that picture
  // unless compared with the character's reference cluster.
  const answer = toPicture.value
    ? await rankSheets(props.character.id, order.value, [drawnFrom.value])
    : await rankSheets(props.character.id, order.value);
  if (run !== rankRun) return;
  rankedAgainst.value = answer.references || 0;
  ranking.value = Object.fromEntries(
    answer.sheets.map((sheet) => [sheet.picture_id, sheet]),
  );
}

// The polling stops once every sheet is read: a switch scores them again.
watch(compareTo, () => {
  if (!order.value.length) return;
  rank().catch((e) => console.warn("Couldn't rank the character sheets", e));
});

async function tick() {
  timer = null;
  ticking = true;
  try {
    await followRuns();
    if (order.value.length) await rank();
  } catch (e) {
    console.warn("Couldn't rank the character sheets", e);
  } finally {
    ticking = false;
  }
  if (!alive) return;
  if (!runsEnded.value) runsEndedAt = 0;
  else if (!runsEndedAt) runsEndedAt = Date.now();
  if (runsEndedAt && Date.now() - runsEndedAt > SHEET_FACE_WAIT_MS) {
    gaveUpOnFaces.value = true;
  }
  // Drawn from a picture, only a sheet close to it is kept for the user.
  const best =
    drawnFrom.value && !item.value
    ? holding.value.find((sheet) => sheet.close)
    : holding.value[0];
  if (!keptTouched.value && !kept.value.length && best) {
    kept.value = [best.picture_id];
  }
  if (allEnded.value) {
    if (phase.value === "drawing") phase.value = "pick";
    return;
  }
  schedule();
}

/** Stop one sheet's run from its place; it then reads "stopped". */
async function stopSheetRun(run) {
  run.stopping = true;
  // Nothing stopped (it ended, or ComfyUI refused): the place is as it was.
  run.stopping = (await tasks.stopComfyuiRun(run.key)) > 0;
  schedule();
}

/** Stop every run of this draw still going, and those a new draw set aside
 * that Tasks still lists; the sheets already drawn stay in the Scrapheap. */
function stopAll() {
  const live = new Set(tasks.comfyuiRuns.map((run) => runKey(run)));
  const keys = new Set([
    ...waitingRuns.value.map((run) => run.key),
    ...strayKeys.value.filter((key) => live.has(key)),
  ]);
  for (const key of keys) tasks.stopComfyuiRun(key);
}

async function draw() {
  if (working.value || !canDraw.value) return;
  working.value = true;
  error.value = "";
  // What the draw runs with, read before any await: a choice changed while
  // the prompt is built must not reach this run (the sizes are the checked
  // workflow's own).
  const workflow = workflowId.value;
  const sizes = workflowSizes.value;
  const lora = chosenLora.value;
  const weight = strength.value;
  const count = form.count;
  const reference = fromReference.value ? referenceId.value : null;
  const inputs = workflowInputs.value;
  const sheetLayout = layout.value;
  // Where the group shows it is the photo look's On/Off: off adds no look.
  // An item's sheet takes no look from the run, and the graph's own sampler.
  const grouped =
    !item.value && Boolean(sheetAssist.value) && sheetLayout === "prose";
  const assist = grouped ? sheetLook.request() : null;
  const photoLook = grouped ? false : form.photo_look;
  // LCM is the graph as saved, so only Euler is sent.
  const euler =
    !item.value && sheetSamplerChoice.value && sampler.value === "euler";
  rememberRecent();
  try {
    const description = form.description.trim();
    if (!item.value && !savedDescription.value && description) {
      await patchCharacter(props.character.id, { description });
      savedDescription.value = description;
    }
    const sheet = await buildSheetPrompt({
      description,
      top: form.top,
      trousers: form.trousers,
      shoes: form.shoes,
      background: form.background,
      framing: form.framing,
      size: form.size,
      proportions: form.proportions,
      // The route ignores it for any layout but prose.
      photo_look: photoLook,
      // The run adds Photo look's lines, so the sheet prompt adds none.
      ...(assist ? { look_added_by_run: true } : {}),
      trigger: trigger.value,
      // From a reference, the sentence names the person in the picture.
      ...(reference ? { reference: true } : {}),
      ...(sheetLayout ? { layout: sheetLayout } : {}),
      // An item's sheet is its description alone; the route ignores the rest.
      ...(item.value ? { kind: props.kind } : {}),
    });
    const drawing = JSON.stringify([
      sheet.prompt,
      workflow,
      lora?.sha256 || "",
      weight,
      reference,
      assist,
      euler,
    ]);
    const answer = await runWorkflowCard({
      workflow_id: workflow,
      prompt: sheet.prompt,
      negative: sheet.negative,
      values: sizes.map((d) => ({
        slot_label: d.slot_label,
        input_name: d.input_name,
        value: d.input_name === "width" ? sheet.width : sheet.height,
      })),
      add_loras: lora
        ? [
            {
              sha256: lora.sha256,
              strength_model: weight,
              strength_clip: weight,
            },
          ]
        : [],
      ...(reference
        ? {
            inputs: inputs.map((input) => ({
              slot_label: input.slot_label,
              input_name: input.input_name,
              picture_id: reference,
            })),
          }
        : {}),
      count,
      // A sheet is six photos of one person: none of its faces is anybody's
      // until it is chosen and cut, so nothing here names the character.
      character_ids: [],
      // Not the library's until kept: imported straight to the Scrapheap.
      hold: true,
      label: item.value
        ? `${item.value.title.replace(/s$/, "")} · ${itemName.value}`
        : `Character sheet · ${name.value}`,
      ...(assist ? { prompt_assist: assist } : {}),
      ...(euler ? { sampler: "euler" } : {}),
    });
    const started = (answer?.prompts || []).filter((p) => runKey(p));
    if (!started.length) {
      throw new Error(
        answer?.error ||
          "The workflow can't run as it is. Open it from Workflows to see why.",
      );
    }
    if (drawing !== drawnWith.value) {
      // The sheets on show are of another prompt: they stay in the Scrapheap.
      startOver();
      drawnWith.value = drawing;
      drawnFrom.value = reference;
    }
    runs.value.push(
      ...started.map((p) => ({
        key: runKey(p),
        status: p.status === "failed" ? "failed" : "queued",
        message: "",
      })),
    );
    runHistory.value.push(...started.map((p) => runKey(p)));
    sheetLook.recordRun();
    phase.value = "drawing";
    gaveUpOnFaces.value = false;
    schedule();
    showSlot(runKey(started[0]));
  } catch (e) {
    error.value = `Couldn't start the sheets. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}

/** Scroll the place of run *key* into view: *Draw N more* adds its places
 * below the sheets on show, out of sight on a short window. */
function showSlot(key) {
  nextTick(() => {
    const slot = [...(mainPane.value?.querySelectorAll("[data-slot]") || [])].find(
      (el) => el.dataset.slot === key,
    );
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    slot?.scrollIntoView?.({ block: "start", behavior: still ? "auto" : "smooth" });
  });
}

/** Forget every sheet and run: a new pick starts empty. */
function startOver() {
  strayKeys.value = [
    ...new Set([...strayKeys.value, ...waitingRuns.value.map((run) => run.key)]),
  ];
  runs.value = [];
  order.value = [];
  ranking.value = {};
  cutEdits.value = {};
  kept.value = [];
  keptTouched.value = false;
  runsEndedAt = 0;
  drawnWith.value = "";
  cutDone.value = false;
  drawnFrom.value = null;
  rankedAgainst.value = 0;
}

/** Store a copy of *card*, an edit workflow, that draws at the sheet's size,
 * and offer the mode it makes possible: the library's convertible card, or
 * a reference card that draws at its picture's size. */
async function makeSheetWorkflow(card = convertible.value) {
  if (working.value || !card) return;
  working.value = true;
  error.value = "";
  try {
    await sheetCopyWorkflow(card.id);
    const { cards } = await listWorkflowCards();
    if (!alive) return;
    sortCards(cards);
    // The copy is the only reference card that can draw: it is chosen.
    await checkCards();
    // With no text workflow the new mode is the only one: go to it, as load() does.
    if (!modeCards.value.length && referenceCards.value.length) {
      mode.value = "reference";
    }
  } catch (e) {
    error.value = `Couldn't make the sheet workflow. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}

/** Store a sheet copy of *card*, a text-to-image workflow, that draws the
 * sheet's layout as regions on its own model, and choose it. Offered in the
 * text mode only, so the mode stands. */
async function makeTextSheetWorkflow(card) {
  if (working.value || !card) return;
  working.value = true;
  error.value = "";
  try {
    const copy = await sheetCopyWorkflow(card.id);
    const { cards } = await listWorkflowCards();
    if (!alive) return;
    sortCards(cards);
    if (copy?.workflow_id) workflowId.value = copy.workflow_id;
    // Chosen only once its pre-flight passes, as every card is.
    await checkCards();
  } catch (e) {
    error.value = `Couldn't make the sheet workflow. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}

async function use() {
  const keep = keptSheets.value.map((sheet) => sheet.picture_id);
  if (working.value || !keep.length) return;
  working.value = true;
  error.value = "";
  phase.value = "filing";
  // The boxes of each kept sheet that has one drawn by hand, all six as they
  // are on screen; a sheet with none is cut as the cutter finds it.
  const boxes = Object.fromEntries(
    keptSheets.value
      .filter((sheet) => sheetEdited(sheet))
      .map((sheet) => [sheet.picture_id, cellsOf(sheet)]),
  );
  if (item.value) {
    await useItem(keep, boxes);
    return;
  }
  let ids;
  try {
    ({ picture_ids: ids } = await useSheets(props.character.id, keep, boxes));
  } catch (e) {
    phase.value = "pick";
    error.value = `Couldn't use the sheets. ${errorMessage(e, "Please try again.")}`;
    working.value = false;
    return;
  }
  // Cut: the build stores the new pictures now, and never offers this cut
  // again, even if the screen goes before their faces are read.
  cutDone.value = true;
  emit("cut", ids);
  // Only the panels' faces may still be unread. A failure from here on is no
  // reason to offer the cut again.
  const deadline = Date.now() + FACE_WAIT_MS;
  while (alive && Date.now() < deadline) {
    try {
      const answer = await rankSheets(props.character.id, ids);
      if (answer.sheets.every((sheet) => sheet.faces_read)) break;
    } catch (e) {
      console.warn("Couldn't read the new pictures' faces", ids, e);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  working.value = false;
  if (!alive) return;
  // The sheets are used: a return through the step row starts a new draw.
  startOver();
  phase.value = "describe";
  emit("done", ids);
}

/** Cut the kept item sheets and put their panels in a new set named for the
 * item: an outfit's under its person. The set is the item until the Studio
 * keeps outfits, locations and props of its own. The set is made first, so a
 * name already taken stops the use before anything is cut, and a cut that
 * fails takes its empty set away again. */
async function useItem(keep, boxes) {
  let set;
  try {
    ({ picture_set: set } = await createPictureSet({
      name: itemName.value,
      description: form.description.trim(),
      ...(props.kind === "outfit" && props.character
        ? { parent_character_id: props.character.id }
        : {}),
    }));
  } catch (e) {
    // The name field is on the form; the sheets wait behind "Back to the sheets".
    phase.value = "describe";
    error.value = `Couldn't make the set ${itemName.value}. ${errorMessage(e, "Please try again.")} Edit the name, then go back to the sheets and use them.`;
    working.value = false;
    return;
  }
  // The set is the item: the Studio lists it as one (#2819). A refusal here
  // leaves an ordinary set, which the Studio can still take with Add a set.
  try {
    await listSetInStudio(set.id, props.kind);
  } catch (e) {
    console.warn("Couldn't list the new set in the Studio", set.id, props.kind, e);
  }
  let ids;
  try {
    ({ picture_ids: ids } = await useItemSheets(props.kind, keep, boxes));
  } catch (e) {
    try {
      await deletePictureSet(set.id);
    } catch (removal) {
      console.warn("Couldn't remove the empty set of a failed cut", set.id, removal);
    }
    phase.value = "pick";
    error.value = `Couldn't use the sheets. ${errorMessage(e, "Please try again.")}`;
    working.value = false;
    return;
  }
  cutDone.value = true;
  emit("cut", ids);
  try {
    await bulkAddPicturesToSet(set.id, ids);
  } catch (e) {
    // The pictures are in the library and the set exists; only the filing failed.
    console.warn("Couldn't file the sheet pictures in their set", set.id, ids, e);
    error.value = `The pictures were made but couldn't be put in ${set.name}. ${errorMessage(e, "Add them to it by hand.")}`;
    set = null;
  }
  working.value = false;
  if (!alive) return;
  startOver();
  phase.value = "describe";
  emit("done", ids, set);
}

const canDraw = computed(
  () =>
    loaded.value &&
    !!workflowId.value &&
    workflowProblem.value === "" &&
    // The picture says who; in words alone the description has to.
    (fromReference.value ? !!referenceId.value : !!form.description.trim()) &&
    phase.value !== "filing",
);

/** Kept sheets whose faces look like different people, as pairs of numbers.
 * Without a LoRA each sheet is its own person, so keeping two usually mixes
 * faces; with one, several sheets can be the same person and pass. */
const mixedPairs = computed(() => {
  const kept = keptSheets.value;
  const pairs = [];
  for (const sheet of kept) {
    for (const id of sheet.different_from || []) {
      const other = kept.find((k) => k.picture_id === id);
      if (other && sheet.n < other.n) pairs.push([sheet.n, other.n]);
    }
  }
  return pairs;
});

const mixedWarning = computed(() => {
  const pairs = mixedPairs.value;
  if (!pairs.length) return "";
  if (pairs.length === 1) {
    return `Sheets ${pairs[0][0]} and ${pairs[0][1]} look like different people; using both mixes faces.`;
  }
  return "Kept sheets look like different people; using them mixes faces.";
});

const keptWords = computed(() => {
  const ns = keptSheets.value.map((sheet) => sheet.n);
  if (ns.length === 1) return `Sheet ${ns[0]}`;
  return `Sheets ${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
});

const keepLabel = computed(() => {
  const count = keptSheets.value.length;
  if (count === 0) return "Use a sheet";
  if (count === 1) return `Use sheet ${keptSheets.value[0].n}`;
  return `Use ${count} sheets`;
});

const footer = computed(() => {
  const n = form.count;
  if (phase.value === "describe") {
    return {
      lead: item.value
        ? `The ${item.value.noun}'s pictures go in a new set, ${itemName.value}`
        : `Pictures are filed on ${name.value}`,
      back: order.value.length
        ? { label: "Back to the sheets", run: () => (phase.value = "pick") }
        : null,
      primary: {
        label: `Draw ${n} ${n === 1 ? "sheet" : "sheets"}`,
        icon: "draw",
        enabled: canDraw.value,
        run: draw,
      },
    };
  }
  if (phase.value === "filing") {
    return {
      lead: item.value
        ? "Cutting the sheets into pictures…"
        : "Cutting the sheets into pictures and reading their faces…",
      primary: { label: keepLabel.value, enabled: false, run: use },
    };
  }
  const waiting = waitingRuns.value.length;
  return {
    // Warned, not refused: the owner may want two looks of one character.
    warn: !!mixedWarning.value,
    lead:
      mixedWarning.value ||
      (waiting
        ? `Drawing ${waiting} more, about a minute each.`
        : keptSheets.value.length
          ? `${keptWords.value} ${keptSheets.value.length === 1 ? "becomes" : "become"} ${item.value ? `${itemName.value}'s ${item.value.angles.join(", ")}` : `${name.value}'s pictures`}; the sheets stay in the Scrapheap.`
          : "Keep the sheets to use. Every sheet stays in the Scrapheap."),
    // The rail's Edit goes back to the form too; the footer offers it only
    // when no sheet is worth using.
    back: noneHold.value
      ? { label: "Edit the description", run: () => (phase.value = "describe") }
      : null,
    secondary: {
      label: `Draw ${n} more`,
      enabled: canDraw.value && !working.value,
      run: draw,
    },
    primary: {
      label: keepLabel.value,
      enabled:
        keptSheets.value.length > 0 &&
        keptSheets.value.every((s) => s.faces_read || gaveUpOnFaces.value) &&
        !working.value,
      run: use,
    },
  };
});

/** The engine *card* draws a sheet with, or null. Read off its diffusion
 * model only: a Krea 2, MOSS-Image or Z-Image graph loads a Qwen text
 * encoder, and matching on that is how Krea 2's style reference got listed. */
function cardEngine(card) {
  const model = (card?.models || []).find((m) =>
    ["unet", "checkpoint"].includes(m.kind),
  );
  const family = model?.base_model_family;
  if (family === "flux2") {
    const klein = KLEIN.test(`${model.base_model || ""} ${model.filename || ""}`);
    return klein ? ENGINES.klein : null;
  }
  return Object.values(ENGINES).find((e) => e.family === family) || null;
}

/** The engine *card* draws a sheet from a reference picture with, or null:
 * the models that keep the person in the picture. */
function referenceEngine(card) {
  const engine = cardEngine(card);
  return [
    ENGINES.qwen,
    ENGINES.klein,
    ENGINES.minimax,
    ENGINES.minimaxImage,
  ].includes(engine)
    ? engine
    : null;
}

/** The workflow cards as the server listed them, for the cache. */
let cardRows = [];
/** The cards each mode can draw with, most recently used first. */
function sortCards(cards) {
  const recent = (a, b) =>
    String(b.last_used || "").localeCompare(String(a.last_used || ""));
  cardRows = cards;
  // An item sheet is prose only. MiniMax H3 Image is the model it starts on
  // (no item sheet has been probed on any model yet), so it is listed first,
  // and a shipped workflow ahead of the owner's own: one of theirs may run
  // several samplers and save each.
  const itemFirst = (a, b) =>
    item.value
      ? Number(cardEngine(b) === ENGINES.minimaxImage) -
          Number(cardEngine(a) === ENGINES.minimaxImage) ||
        Number(!!b.built_in) - Number(!!a.built_in)
      : 0;
  workflows.value = cards
    .filter(
      (card) =>
        card.type === "txt2img" &&
        !card.reference_inputs &&
        (item.value
          ? !card.sheet_layout &&
            !!cardEngine(card) &&
            cardEngine(card) !== ENGINES.moss
          : card.sheet_layout || cardEngine(card)),
    )
    .sort((a, b) => itemFirst(a, b) || recent(a, b));
  textConvertibles.value = cards
    .filter(
      (card) =>
        card.type === "txt2img" &&
        !card.reference_inputs &&
        !card.sheet_layout &&
        cardEngine(card) !== ENGINES.moss,
    )
    .sort(recent);
  // An item is drawn from words alone.
  referenceCards.value = item.value
    ? []
    : cards
        .filter((card) => card.reference_inputs > 0 && referenceEngine(card))
        .sort(recent);
  // An edit workflow that draws on its picture's own canvas: a sheet copy of
  // it is what the reference mode runs.
  convertible.value =
    item.value || referenceCards.value.length
      ? null
      : cards
          .filter((card) => card.type === "img2img" && referenceEngine(card))
          .sort(recent)[0] || null;
}

/** Show the workflows of the last full check at once. Their checks are run
 * again by `load`, which replaces each answer as it lands. */
function restoreCardCache() {
  const remembered = readCardCache();
  if (!remembered?.cards?.length) return;
  sortCards(remembered.cards);
  checks.value = remembered.checks || {};
  checksDone.value = true;
  workflowsLoaded.value = true;
  // The mode rule `load` applies once the references are read, from the last
  // read of this character's faces.
  const faces = !!props.character && remembered.faces?.[props.character.id] === true;
  mode.value =
    referenceCards.value.length && (faces || !workflows.value.length)
      ? "reference"
      : "text";
  if (!workflowId.value) workflowId.value = usableCards.value[0]?.id || "";
}

async function load() {
  // An item reads no character: no description, LoRA or reference faces.
  const id = item.value ? null : props.character.id;
  const none = Promise.resolve(null);
  restoreCardCache();
  const [character, adapters, cards, references] = await Promise.allSettled([
    id == null ? none : getCharacter(id),
    id == null ? Promise.resolve([]) : listAdapters({ characterId: id }),
    // The shipped sheet workflows are cards already: start-up lists every
    // built-in the owner has not removed.
    listWorkflowCards(),
    id == null ? none : getReferencePictures(id),
  ]);
  if (!alive) return;
  // Read, so drawable as soon as a workflow's check passes, not once all have.
  loaded.value = character.status === "fulfilled";
  if (!loaded.value) {
    error.value = `Couldn't read ${name.value}. Open the set again to try again.`;
  }
  if (character.status === "fulfilled") {
    savedDescription.value = (character.value?.description || "").trim();
    if (!form.description) form.description = savedDescription.value;
  }
  if (adapters.status === "fulfilled") {
    loras.value = [...adapters.value].sort((a, b) =>
      String(b.added_at || "").localeCompare(String(a.added_at || "")),
    );
    // The newest LoRA that loads into the sheet workflow is preselected.
    loraSha.value = fittingLora();
  }
  if (references.status === "fulfilled" && references.value) {
    showReferences(references.value);
  }
  if (cards.status === "fulfilled") {
    sortCards(cards.value.cards);
    // A character with a face is drawn from it where a workflow can; one
    // with no pictures, from words.
    // Both ways: a mode the last open left (restoreCardCache) is re-read here,
    // unless the owner has picked a mode or a workflow of it since.
    if (!picked.has("mode") && !picked.has("workflow")) {
      mode.value =
        referenceCards.value.length &&
        (referenceIds.value.length || !workflows.value.length)
          ? "reference"
          : "text";
    }
    await checkCards();
  }
  workflowsLoaded.value = true;
  // What `restore` puts back is not a choice made: its watchers run after it
  // has set `restored`, so they are let run before choices are remembered.
  restoring = true;
  await restore(props.saved || recalled());
  await nextTick();
  restoring = false;
  for (const result of [character, adapters, cards, references]) {
    if (result.status === "rejected") {
      console.warn("Couldn't load part of the sheet form", result.reason);
    }
  }
}

/** Put back what `snapshot` stored, over the choices `load` defaulted. */
/** Put back the sheets, the form and where the step was, as soon as the
 * screen opens: none of it waits for the step's own reads. */
function restoreState(saved) {
  if (!saved) return;
  framingTouched.value = !!saved.framing_touched;
  proportionsTouched.value = !!saved.proportions_touched;
  // A build saved when the build had a field of its own joins the description.
  const { body = "", ...fields } = saved.form || {};
  Object.assign(form, fields);
  if (body.trim()) {
    form.description = [form.description.trim(), body.trim()]
      .filter(Boolean)
      .join(", ");
  }
  runs.value = (saved.runs || []).map((run) => ({
    key: run.key,
    status: run.status,
    message: run.message || "",
    pictureIds: run.picture_ids || [],
    stopped: !!run.stopped,
  }));
  order.value = [...(saved.order || [])];
  ranking.value = { ...(saved.ranking || {}) };
  kept.value = [...(saved.kept || [])];
  keptTouched.value = !!saved.kept_touched;
  cutEdits.value = { ...(saved.cut_edits || {}) };
  drawnWith.value = saved.drawn_with || "";
  drawnFrom.value = saved.drawn_from ?? null;
  compareTo.value = saved.compare_to === "cluster" ? "cluster" : "picture";
  runHistory.value = [...(saved.run_history || [])];
  drawnIds.value = [...(saved.drawn_ids || [])];
  strayKeys.value = [...(saved.stray_run_keys || [])];
  phase.value = ["drawing", "pick"].includes(saved.phase) ? saved.phase : "describe";
}

/** Put back the choices that name what `load` read (the mode, workflow,
 * LoRA and reference picture) over the defaults it picked, never over one
 * the owner picked while `load` was reading. */
async function restore(saved) {
  if (saved && alive) {
    const kept = (key) => !picked.has(key);
    // A workflow is picked from its mode's list: the mode stays with it.
    if (kept("mode") && kept("workflow") && saved.mode && saved.mode !== mode.value) {
      mode.value = saved.mode;
      // The mode's own watcher picks its first workflow and a fitting LoRA;
      // the saved ones go over those.
      await nextTick();
    }
    if (
      kept("workflow") &&
      usableCards.value.some((card) => card.id === saved.workflow_id)
    ) {
      workflowId.value = saved.workflow_id;
      // A workflow of the other model re-picks a fitting LoRA; the saved
      // one goes over that.
      await nextTick();
    }
    if (
      kept("lora") &&
      (saved.lora_sha === "" ||
        loras.value.some((lora) => lora.sha256 === saved.lora_sha))
    ) {
      loraSha.value = saved.lora_sha;
    }
    if (kept("strength") && saved.strength != null) strength.value = saved.strength;
    if (kept("reference") && saved.reference_id != null) {
      referenceId.value = saved.reference_id;
    }
  }
  restored.value = true;
}

/** The remembered choices as `restore` reads them, or null: a mode only where
 * it can draw (from a reference only for a person with a face, as `load`
 * decides), and this person's own LoRA. */
function recalled() {
  if (!remembered) return null;
  const usable = {
    text: workflows.value.length,
    reference:
      referenceCards.value.length &&
      (referenceIds.value.length || !workflows.value.length),
  };
  const pick = usable[remembered.mode] ? remembered.mode : mode.value;
  const lora = props.character ? remembered.loras?.[props.character.id] : null;
  return {
    mode: pick,
    workflow_id: remembered.workflows?.[choiceKey(pick)],
    lora_sha: lora?.sha,
    strength: lora?.strength,
  };
}

function readRecent(choices) {
  return Object.fromEntries(
    RECENT_FIELDS.map((field) => {
      const values = choices.recent?.[field];
      return [
        field,
        Array.isArray(values)
          ? values.filter((v) => typeof v === "string")
          : [],
      ];
    }),
  );
}

/** Put what this draw wears and stands in front of first in each field's
 * list. Shoes only at full body, the one framing that shows them. */
function rememberRecent() {
  // An item shows none of the fields.
  if (item.value) return;
  const fields = RECENT_FIELDS.filter(
    (field) => field !== "shoes" || form.framing === "full_body",
  );
  remember((all) => {
    const lists = readRecent(all);
    for (const field of fields) {
      lists[field] = withRecent(lists[field], String(form[field] || "").trim());
    }
    // Photo look's typed entries share the document: keep them.
    all.recent = { ...all.recent, ...lists };
    recent.value = lists;
  });
}

/** Remember one choice for the next fresh step: only one made once the
 * step is restored, so a default is never remembered as a choice. */
function remember(update) {
  if (!restored.value || restoring) return;
  writeChoices(update);
}

// A character's mode only: an item's must not change how the next
// character sheet opens.
watch(mode, (now) =>
  item.value ||
  remember((all) => {
    all.mode = now;
  }),
);
watch(workflowId, (id) => {
  if (!id) return;
  remember((all) => {
    all.workflows = { ...all.workflows, [choiceKey(mode.value)]: id };
  });
});
watch(
  () => form.size,
  (size) =>
    remember((all) => {
      all.size = size;
      all.size_family = engine.value.family;
    }),
);
watch(
  () => form.count,
  (count) =>
    item.value ||
    remember((all) => {
      all.count = count;
    }),
);
watch(
  () => form.photo_look,
  (look) =>
    remember((all) => {
      all.photo_look = look;
    }),
);
// Per person. A LoRA left at the default is forgotten, so a newer LoRA of the
// person is still the one taken.
watch([loraSha, strength], ([sha, value]) =>
  props.character &&
  !item.value &&
  remember((all) => {
    const loras = { ...all.loras };
    if (sha === fittingLora() && value === 0.8) delete loras[props.character.id];
    else loras[props.character.id] = { sha, strength: value };
    all.loras = loras;
  }),
);

/** What the set stores of this step. A cut whose faces are still being read
 * stores as done (no runs, no sheets), so the cut is never offered twice. */
const snapshot = computed(() => {
  if (!restored.value) return null;
  const cut = phase.value === "filing" && cutDone.value;
  return {
    phase: cut
      ? "describe"
      : phase.value === "filing"
        ? "pick"
        : phase.value,
    form: { ...form },
    framing_touched: framingTouched.value,
    proportions_touched: proportionsTouched.value,
    mode: mode.value,
    workflow_id: workflowId.value,
    lora_sha: loraSha.value,
    strength: strength.value,
    reference_id: referenceId.value,
    runs: cut
      ? []
      : runs.value.map((run) => ({
          key: run.key,
          status: run.status,
          message: run.message || "",
          picture_ids: run.pictureIds || [],
          stopped: !!run.stopped,
        })),
    order: cut ? [] : order.value,
    // The sheets' scores, so they show at once when the screen opens again;
    // they are ranked afresh behind them.
    ranking: cut ? {} : ranking.value,
    kept: cut ? [] : kept.value,
    kept_touched: cut ? false : keptTouched.value,
    // The photo boxes drawn by hand, kept with the sheets they belong to.
    cut_edits: cut ? {} : cutEdits.value,
    drawn_with: cut ? "" : drawnWith.value,
    drawn_from: cut ? null : drawnFrom.value,
    compare_to: compareTo.value,
    // The runs still going, which deleting the set stops; one that ended is
    // skipped there.
    run_keys: [
      ...strayKeys.value,
      ...(cut ? [] : waitingRuns.value.map((run) => run.key)),
    ],
    stray_run_keys: strayKeys.value,
    run_history: runHistory.value,
    drawn_ids: drawnIds.value,
  };
});

function showReferences(body) {
  referenceIds.value = (body?.reference_picture_ids || []).slice(0, 4);
  if (!picked.has("reference")) referenceId.value = referenceIds.value[0] ?? null;
}

/** The reference faces again, after the reference cluster changed. */
async function reloadReferences() {
  try {
    showReferences(await getReferencePictures(props.character.id));
  } catch (e) {
    console.warn("Couldn't reload the reference faces", e);
  }
}

restoreState(props.saved);
onMounted(() => {
  load();
  // Runs may have ended while the screen was away: read them and rank again.
  if (runs.value.length || order.value.length) tick();
});
onBeforeUnmount(() => {
  alive = false;
  clearTimeout(recheckTimer);
  stopPolling();
});

// While a sheet is zoomed the dialog does not take Escape; the zoom does.
// The picture picker is a dialog over this one, and takes Escape the same way.
const zooming = computed(() => !!zoomed.value || pickerOpen.value);

defineExpose({
  // The parents' toolbar select is the owner picking a workflow.
  workflowId: computed({
    get: () => workflowId.value,
    set: (id) => {
      picked.add("workflow");
      workflowId.value = id;
    },
  }),
  workflowOptions,
  workflowsPending,
  footer,
  error,
  working,
  loraName,
  phase,
  zooming,
  reloadReferences,
  snapshot,
  stopAll,
});
</script>

<style scoped>
.cs-split {
  flex: 1;
  min-height: 0;
  display: flex;
}

.cs-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.cs-pane--side {
  flex: 0 0 280px;
  gap: var(--space-4);
  border-left: 1px solid rgb(var(--v-theme-divider));
}

.cs-pane--wide {
  flex-basis: 320px;
}

.cs-step-h {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.cs-step-h h3 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.cs-step-h span,
.cs-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-hint {
  font-size: var(--text-2xs);
}

.cs-hint b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-fld {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

.cs-opts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-5);
}

.cs-opt {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

/* The Photo look's place while the workflows are checked: a skeleton of the
   group's size, so the step opens on the layout it keeps. */
.cs-look-pending {
  min-height: calc(var(--space-7) * 4);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.cs-fld--grow {
  flex: 1 1 auto;
}

.cs-fld--grow :deep(.app-textarea),
.cs-fld--grow :deep(.app-textarea__box) {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
}

.cs-fld--grow :deep(.app-textarea__field) {
  flex: 1 1 auto;
  resize: none;
}

.cs-lora {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.cs-sl {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.cs-sl__lbl {
  min-width: 56px;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-range {
  flex: 1;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.cs-sl__val {
  min-width: 4ch;
  text-align: right;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.cs-notice {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.cs-notice .v-icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-notice--warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
}

.cs-notice--bad .v-icon {
  color: rgb(var(--v-theme-surface-error));
}

.cs-sheets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-4) var(--space-3);
}

.cs-sheets li {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-sheetimg {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 5 / 3;
  padding: 0;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
}

.cs-split--portrait .cs-sheetimg,
.cs-split--portrait .cs-rail__sheet {
  aspect-ratio: 4 / 5;
}

.cs-sheetimg:disabled {
  cursor: default;
}

.cs-sheetimg img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.cs-sheetimg--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.cs-sheetimg--wait {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  border: 1px dashed rgb(var(--v-theme-border));
  background: transparent;
  cursor: default;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* Shown on hover or focus, as the zoom control is: the pending sheet's stop. */
.cs-link.cs-stop {
  margin-left: 0;
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
}

.cs-sheetimg--wait:hover .cs-stop,
.cs-stop:focus-visible {
  opacity: 1;
}

@media (hover: none) {
  .cs-link.cs-stop {
    opacity: 1;
  }
}

/* The run's percent, as the Tasks panel's run line draws it. */
.cs-progress {
  width: 50%;
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.cs-progress span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-primary));
  transition: width var(--dur-3) var(--ease-standard);
}

.cs-sheetimg__rk,
.cs-sheetimg__ck {
  position: absolute;
  top: var(--space-2);
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.cs-sheetimg__rk {
  left: var(--space-2);
  min-width: 18px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

.cs-sheetimg__ck {
  right: var(--space-2);
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

/* A photo's own score under its face: the rank badge's chip, in mono. One
   that drifts takes the warning colour, on the deeper scrim a chromatic glyph
   needs. */
.cs-pscore {
  position: absolute;
  height: 18px;
  display: inline-flex;
  align-items: center;
  padding: 0 var(--space-2);
  transform: translateX(-50%);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}

.cs-pscore--off {
  background: var(--scrim-photo-strong);
  color: rgb(var(--v-theme-dark-surface-warning));
}

/* A photo the sheet cuts into, on the zoomed sheet: a light line on the
   photo scrim's halo reads over a white gutter and a dark photo alike. One
   that may not cut cleanly is dashed, in the warning colour a drifting
   photo's score takes, so the state is not colour alone. Inside the box, so
   two photos cut with no gutter keep both lines. */
.cs-cell {
  position: absolute;
  box-sizing: border-box;
  border: 2px solid rgb(var(--v-theme-on-dark-surface));
  box-shadow: inset 0 0 0 1px var(--scrim-photo-strong);
}

.cs-cell--unclean {
  border-style: dashed;
  border-color: rgb(var(--v-theme-dark-surface-warning));
}

/* A photo the person drew: a double line in the accent colour, so it reads
   as edited by its line as well as its colour. The zoom's overlay lets no
   pointer through except to an editable photo. */
.cs-cell--edited {
  border: 4px double rgb(var(--v-theme-primary));
}

.cs-cell--editable {
  pointer-events: auto;
  cursor: move;
}

/* The grips on an editable photo: its corners and edge midpoints. */
.cs-handle {
  position: absolute;
  width: var(--space-2);
  height: var(--space-2);
  transform: translate(-50%, -50%);
  border: 1px solid var(--scrim-photo-strong);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-on-dark-surface));
}

.cs-handle--nw,
.cs-handle--se {
  cursor: nwse-resize;
}

.cs-handle--ne,
.cs-handle--sw {
  cursor: nesw-resize;
}

.cs-handle--n,
.cs-handle--s {
  cursor: ns-resize;
}

.cs-handle--e,
.cs-handle--w {
  cursor: ew-resize;
}

.cs-handle--nw,
.cs-handle--w,
.cs-handle--sw {
  left: 0;
}

.cs-handle--n,
.cs-handle--s {
  left: 50%;
}

.cs-handle--ne,
.cs-handle--e,
.cs-handle--se {
  left: 100%;
}

.cs-handle--nw,
.cs-handle--n,
.cs-handle--ne {
  top: 0;
}

.cs-handle--w,
.cs-handle--e {
  top: 50%;
}

.cs-handle--sw,
.cs-handle--s,
.cs-handle--se {
  top: 100%;
}

.cs-cap {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-cap b,
.cs-item b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

/* The reference picture, large: what the sheets are judged against. Every
   picture here is letterboxed on its tile's surface, as in the picture
   picker: a crop cuts a full-body reference down to its torso. */
.cs-cbig {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 5;
  object-fit: contain;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.cs-cstrip {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--space-2);
}

.cs-reft {
  position: relative;
  aspect-ratio: 1 / 1;
  padding: 0;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
}

.cs-reft img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.cs-reft--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.cs-reft--add {
  display: grid;
  place-items: center;
  border: 1px dashed rgb(var(--v-theme-border));
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-reft--add:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* A sheet's closeness: its name, a bar, the number, the words. */
.cs-ccap {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.cs-cline {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-cline b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

/* Data wears teal; a track is a pill. */
.cs-meter {
  flex: 1;
  max-width: 160px;
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.cs-meter span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}

.cs-num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

/* The reference beside a zoomed sheet, at a passport photo's size. */
.cs-zoomref {
  flex: 0 0 240px;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  overflow-y: auto;
}

.cs-secl {
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-rail {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.cs-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-row1 {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.cs-link--start {
  align-self: flex-start;
  margin-left: 0;
  text-align: left;
}

.cs-link.cs-link--inline {
  margin-left: var(--space-1);
}

.cs-link {
  margin-left: auto;
  padding: 0;
  border: 1px solid transparent;
  background: none;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  cursor: pointer;
}

/* The tile and its zoom control: the check sits top right, the magnifier
   bottom right, so a tick and a zoom never share a target. */
.cs-tile {
  position: relative;
}

.cs-mag {
  position: absolute;
  right: var(--space-3);
  bottom: var(--space-3);
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: zoom-in;
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
}

.cs-tile:hover .cs-mag,
.cs-mag:focus-visible,
.cs-sheetimg:focus-visible + .cs-mag {
  opacity: 1;
}

/* Pointed at, it answers: a deeper backing and a ring, never a fade. Focus
   keeps the app's own focus ring and takes only the backing. */
.cs-mag:hover,
.cs-mag:focus-visible {
  background: var(--scrim-photo-strong);
}

.cs-mag:hover {
  box-shadow: 0 0 0 2px rgb(var(--v-theme-on-dark-surface));
}

/* No hover on touch: the control stays shown. */
@media (hover: none) {
  .cs-mag {
    opacity: 1;
  }
}

/* Kept sheets two across; more rows scroll with the rail. */
.cs-keptgrid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}

.cs-keptgrid li {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  font-size: var(--text-2xs);
}

.cs-keptgrid b {
  font-weight: var(--weight-medium);
}

.cs-desc {
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

.cs-rail__sheet {
  width: 100%;
  aspect-ratio: 5 / 3;
  object-fit: contain;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.cs-faces {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-1);
}

.cs-faces img {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: contain;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
}

.cs-gapbox {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.cs-todo {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.cs-todo > span {
  display: flex;
  gap: var(--space-2);
  align-items: baseline;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-todo b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-n {
  min-width: 12px;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
</style>
