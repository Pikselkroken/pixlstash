<template>
  <div
    class="mv"
    role="region"
    aria-label="Moves made outside PixlStash"
    data-testid="moves-review"
  >
    <!-- The shell's top band, the same 36px recipe as Insights/Duplicates/the
         model shelf. -->
    <div class="mv-toolbar">
      <h2 class="mv-title">Moves made outside PixlStash</h2>
      <span class="mv-sub">{{ subtitle }}</span>
      <div class="mv-tb-right">
        <AppButton
          variant="ghost"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="load"
        >
          Look again
        </AppButton>
      </div>
    </div>

    <div class="mv-scroll">
      <p v-if="error" class="mv-state mv-state--error" role="alert">
        {{ error }}
      </p>

      <p v-else-if="loading && !loaded" class="mv-state">
        Reading the reconciliation queue…
      </p>

      <template v-else>
        <p class="mv-lede">
          The same rule, the other way round. PixlStash moves a file when its
          folder stops being true; when you move a file, PixlStash changes an
          assignment when that assignment stops being true. Nothing else is
          touched.
        </p>

        <p v-if="!store.hasAnyPending" class="mv-state">
          Nothing to reconcile right now.
        </p>

        <!-- Unambiguous: each had exactly one of the thing it was filed by, so
             leaving that folder can only mean one thing. -->
        <section v-if="store.unambiguous.length" class="mv-card mv-card--clear">
          <div class="mv-card-head">
            <div>
              <h3 class="mv-card-title">
                {{ store.unambiguous.length }} said something clear
              </h3>
              <p class="mv-card-sub">
                each had exactly one of the thing it was filed by, so leaving
                that folder can only mean one thing
              </p>
            </div>
            <AppButton
              variant="primary"
              :loading="applyingAll"
              @click="applyAll"
            >
              Apply all {{ store.unambiguous.length }}
            </AppButton>
          </div>
          <ul class="mv-rows">
            <li v-for="item in store.unambiguous" :key="item.review_id" class="mv-row">
              <img
                class="mv-thumb"
                :src="pictureThumbnailUrl(item.picture_id)"
                alt=""
                loading="lazy"
                @error="$event.target.style.visibility = 'hidden'"
              />
              <span class="mv-path" :title="item.new_path">{{
                shortFolder(item.new_path)
              }}</span>
              <span class="mv-change">
                <template v-if="changeShape(item).swap">
                  <span class="mv-etag mv-etag--out">
                    <span class="mv-etag-facet">{{
                      facetLabel(changeShape(item).swap.out.facet)
                    }}</span>
                    {{ changeShape(item).swap.out.name }}
                  </span>
                  <v-icon size="14" class="mv-swap-arrow">mdi-arrow-right</v-icon>
                  <span class="mv-etag mv-etag--in">
                    <span class="mv-etag-facet">{{
                      facetLabel(changeShape(item).swap.in.facet)
                    }}</span>
                    {{ changeShape(item).swap.in.name }}
                  </span>
                </template>
                <template v-else>
                  <span
                    v-for="tag in changeShape(item).tags"
                    :key="`${tag.sign}-${tag.facet}-${tag.name}`"
                    :class="[
                      'mv-etag',
                      tag.sign === '+' ? 'mv-etag--in' : 'mv-etag--out',
                    ]"
                  >
                    {{ tag.sign }}
                    <span class="mv-etag-facet">{{ facetLabel(tag.facet) }}</span>
                    {{ tag.name }}
                  </span>
                </template>
              </span>
              <AppButton
                variant="secondary"
                size="sm"
                :loading="busyIds.has(item.review_id)"
                @click="applyOne(item.review_id)"
              >
                Apply
              </AppButton>
            </li>
          </ul>
        </section>

        <!-- Ambiguous: several of that thing, so leaving one folder does not
             say whether the owner left it or just refiled the picture. -->
        <section v-if="store.ambiguous.length" class="mv-card mv-card--warn">
          <div class="mv-card-head">
            <div>
              <h3 class="mv-card-title">
                {{ store.ambiguous.length }} could mean two things
              </h3>
              <p class="mv-card-sub">
                these are in more than one already, so leaving one folder does
                not say whether you left it or just refiled the picture
              </p>
            </div>
          </div>
          <ul class="mv-rows">
            <li
              v-for="item in store.ambiguous"
              :key="item.review_id"
              class="mv-row mv-row--ambiguous"
            >
              <img
                class="mv-thumb"
                :src="pictureThumbnailUrl(item.picture_id)"
                alt=""
                loading="lazy"
                @error="$event.target.style.visibility = 'hidden'"
              />
              <div class="mv-ambiguous-body">
                <span class="mv-path" :title="item.new_path">{{
                  shortFolder(item.new_path)
                }}</span>
                <span class="mv-current">{{ currentSummary(item) }}</span>
              </div>
              <AppButton
                variant="secondary"
                size="sm"
                :loading="busyIds.has(item.review_id)"
                @click="applyOne(item.review_id)"
              >
                {{ onlyNowLabel(item) }}
              </AppButton>
              <AppButton
                variant="ghost"
                size="sm"
                :loading="busyIds.has(item.review_id)"
                @click="dismissOne(item.review_id)"
              >
                Keep both
              </AppButton>
            </li>
          </ul>
          <p class="mv-note">
            A folder can hold a picture once; a project can share it.
            PixlStash will not turn one into the other by guessing. Untouched
            until you say.
          </p>
        </section>

        <!-- Off-layout: already followed, nothing to decide. -->
        <section v-if="store.offLayout.length" class="mv-card">
          <h3 class="mv-card-title">
            {{ store.offLayout.length }} went somewhere the layout does not
            describe
          </h3>
          <p class="mv-card-sub">
            already followed, nothing to decide. Their tags, scores, people
            and projects are exactly as they were - only the path changed.
          </p>
          <div class="mv-chips">
            <span
              v-for="item in store.offLayout"
              :key="item.review_id"
              class="mv-chip"
              :title="item.new_path"
              >{{ shortFolder(item.new_path) }}</span
            >
          </div>
          <p class="mv-note">
            A folder outside the layout contradicts nothing, so it changes
            nothing and PixlStash will never move these back. Putting a
            picture somewhere of your own is how you overrule all of this,
            and it keeps working.
          </p>
        </section>

        <div v-if="store.hasAnyPending" class="mv-actions">
          <AppButton
            v-if="store.unambiguous.length"
            variant="primary"
            :loading="applyingAll"
            @click="applyAll"
          >
            Apply the {{ store.unambiguous.length }}
          </AppButton>
          <AppButton variant="ghost" :loading="dismissingAll" @click="dismissAll">
            Leave everything as it was
          </AppButton>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import AppButton from "../widgets/AppButton.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useMovesStore } from "../../stores/useMovesStore";
import { errorDetail } from "../../utils/apiError";

const store = useMovesStore();
const loading = computed(() => store.loading);
const loaded = computed(() => store.loaded);
const error = ref("");
const applyingAll = ref(false);
const dismissingAll = ref(false);
const busyIds = ref(new Set());

const FACET_LABELS = { project: "Project", set: "Set", person: "Person" };

function facetLabel(facet) {
  return FACET_LABELS[facet] || facet;
}

/** A short "…/parent/leaf" label for a full path, for a row that has no room
 * for the whole thing and no need to prove it - the tooltip carries the rest. */
function shortFolder(path) {
  const parts = String(path || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean);
  parts.pop(); // drop the file name
  if (!parts.length) return "/";
  const tail = parts.slice(-2).join("/");
  return parts.length > 2 ? `…/${tail}` : tail;
}

/** One removal and one addition of the SAME facet reads as a swap; anything
 * else (a pure addition, a pure removal, or a cross-facet change) reads as
 * separate +/- tags rather than forcing a misleading arrow between them.
 *
 * @returns {{swap: {out: object, in: object}}|{tags: Array<object>}}
 */
function changeShape(item) {
  const removals = item.removals || [];
  const additions = item.additions || [];
  if (
    removals.length === 1 &&
    additions.length === 1 &&
    removals[0].facet === additions[0].facet
  ) {
    return { swap: { out: removals[0], in: additions[0] } };
  }
  return {
    tags: [
      ...removals.map((r) => ({ ...r, sign: "−" })),
      ...additions.map((a) => ({ ...a, sign: "+" })),
    ],
  };
}

/** The resolve button's label - always names what the owner ends up with,
 * never a generic verb, because this button applies a removal.
 *
 * The canonical ambiguous case (the design mock's own example) has NO
 * addition: the picture already belongs to the folder it moved into, so
 * there is nothing new to gain, only the old membership to leave. The
 * destination there is one of the picture's own `current` names for the
 * ambiguous facet - specifically the one that is not being removed - so it
 * is derived from `current`, never left to a fallback string that would say
 * nothing about what clicking the button actually does. */
function onlyNowLabel(item) {
  const addition = (item.additions || [])[0];
  if (addition) return `Only ${addition.name} now`;
  const removal = (item.removals || [])[0];
  if (removal) {
    const remaining = (item.current?.[removal.facet] || []).filter(
      (name) => name !== removal.name,
    );
    if (remaining.length === 1) return `Only ${remaining[0]} now`;
  }
  return "Apply this move";
}

/** "in 2024 Shoots and Client · Nordvik" - why leaving one folder does not
 * say which the owner meant. */
function currentSummary(item) {
  const facets = Object.keys(item.current || {});
  if (!facets.length) return "";
  return facets
    .map((facet) => `in ${(item.current[facet] || []).join(" and ")}`)
    .join("; ");
}

const subtitle = computed(() => {
  if (loading.value && !loaded.value) return "";
  const n = store.pendingCount;
  return n > 0
    ? `${n} move${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} a decision · nothing has been changed yet`
    : "nothing has been changed yet";
});

async function load() {
  error.value = "";
  // fetchPending() catches its own failure into store.error and never
  // rethrows (useMovesStore is also read by the sidebar badge, which must
  // not throw on a failed background poll) - so the failure has to be read
  // back from the store, not from a try/catch here.
  await store.fetchPending();
  if (store.error) error.value = store.error;
}

/** A row cleared from the queue whose change could not actually be made -
 * most commonly a set or person name that stopped being unique between the
 * GET and the click. The row is gone either way (it was acted on by id); this
 * is what stops that from looking identical to a change that worked. */
function warnIfSkipped(result) {
  const skipped = result?.skipped_review_ids?.length || 0;
  if (skipped) {
    error.value = `${skipped} move${skipped === 1 ? "" : "s"} could not be applied - the name it would have used is no longer unique.`;
  }
}

async function applyOne(reviewId) {
  busyIds.value.add(reviewId);
  try {
    warnIfSkipped(await store.applyReview(reviewId));
  } catch (err) {
    error.value = errorDetail(err) || err?.message || "Could not apply that move.";
  } finally {
    busyIds.value.delete(reviewId);
  }
}

async function dismissOne(reviewId) {
  busyIds.value.add(reviewId);
  try {
    await store.dismissReviews([reviewId]);
  } catch (err) {
    error.value = errorDetail(err) || err?.message || "Could not dismiss that move.";
  } finally {
    busyIds.value.delete(reviewId);
  }
}

async function applyAll() {
  applyingAll.value = true;
  try {
    warnIfSkipped(await store.applyAllUnambiguous());
  } catch (err) {
    error.value = errorDetail(err) || err?.message || "Could not apply those moves.";
  } finally {
    applyingAll.value = false;
  }
}

async function dismissAll() {
  dismissingAll.value = true;
  try {
    await store.dismissAll();
  } catch (err) {
    error.value = errorDetail(err) || err?.message || "Could not dismiss the queue.";
  } finally {
    dismissingAll.value = false;
  }
}

onMounted(() => {
  if (!store.loaded) load();
});
</script>

<style scoped>
.mv {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
}

.mv-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 36px;
  box-sizing: border-box;
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.mv-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 6;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mv-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.6);
  white-space: nowrap;
}

.mv-tb-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.mv-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-6) var(--space-7) var(--space-8);
}

.mv-state {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.mv-state--error {
  color: rgb(var(--v-theme-error));
}

.mv-lede {
  margin: 0 0 var(--space-6);
  max-width: 72ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.mv-card {
  max-width: 1120px;
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-surface));
  margin-bottom: var(--space-5);
}

.mv-card--clear {
  border-left: 3px solid rgb(var(--v-theme-success));
}

.mv-card--warn {
  border-left: 3px solid rgb(var(--v-theme-warning));
}

.mv-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.mv-card-title {
  margin: 0 0 var(--space-1);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.mv-card-sub {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.mv-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.mv-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.mv-row--ambiguous {
  align-items: flex-start;
}

.mv-thumb {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  object-fit: cover;
  background: rgba(var(--v-theme-on-background), 0.08);
}

.mv-path {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.6);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mv-ambiguous-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.mv-current {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.5);
}

.mv-change {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.mv-swap-arrow {
  color: rgba(var(--v-theme-on-background), 0.4);
}

.mv-etag {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

.mv-etag--out {
  background: rgba(var(--v-theme-error), 0.12);
  color: rgb(var(--v-theme-error));
}

.mv-etag--in {
  background: rgba(var(--v-theme-success), 0.14);
  color: rgb(var(--v-theme-success));
}

.mv-etag-facet {
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  opacity: 0.75;
}

.mv-note {
  margin: var(--space-4) 0 0;
  padding: var(--space-2) var(--space-4);
  border-left: 3px solid rgb(var(--v-theme-accent));
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--hover-wash);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.mv-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.mv-chip {
  padding: 2px var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-background), 0.06);
  border: 1px solid rgb(var(--v-theme-border));
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.mv-actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-6);
}

@media (max-width: 900px) {
  .mv-sub {
    display: none;
  }
  .mv-scroll {
    padding: var(--space-5) var(--space-5) var(--space-8);
  }
  .mv-row {
    flex-wrap: wrap;
  }
}
</style>
