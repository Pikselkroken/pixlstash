<script setup>
// "Try the headline feature" (#2145): the 2.0 training tour's card at the
// foot of the media sidebar. Training takes most of an hour, so it is a tour
// that points at each click, never a checklist step; it never starts a run.
// Dismissing it leaves the What's changed checklist alone, and the title
// bar's version label brings either back.
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { useWhatsChangedStore } from "../../stores/useWhatsChangedStore";
import shotUrl from "../../assets/training-run-card.jpg";

const whatsChanged = useWhatsChangedStore();
</script>

<template>
  <section class="headline" aria-labelledby="headline-label">
    <h2 id="headline-label" class="section-label headline-label">
      Try the headline feature
    </h2>
    <div class="hl-card">
      <button
        type="button"
        class="hl-x"
        aria-label="Dismiss the training tour card"
        @click="whatsChanged.dismissCard()"
      >
        <Tooltip
          text="Dismiss the training tour card"
          activator="parent"
          :describe="false"
        />
        <v-icon size="14">mdi-close</v-icon>
      </button>
      <img
        class="hl-shot"
        :src="shotUrl"
        alt="A training run: the loss curve with preview pictures under each checkpoint"
      />
      <div class="hl-b">
        <b>Train a LoRA of someone in your library</b>
        <span>A four-step tour that points at each click. It never starts a run.</span>
        <div class="hl-acts">
          <AppButton
            data-testid="headline-show-me"
            :disabled="whatsChanged.tourStep > 0"
            @click="whatsChanged.tourStep = 1"
            >Show me <v-icon size="16">mdi-arrow-right</v-icon></AppButton
          >
          <AppButton variant="ghost" @click="whatsChanged.dismissCard()"
            >Nah, I'll wing it</AppButton
          >
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.headline {
  flex-shrink: 0;
  padding: var(--space-2) var(--space-2) var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.headline-label {
  margin: 0;
  padding: var(--space-2) var(--space-3);
}

.hl-card {
  position: relative;
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}

.hl-shot {
  display: block;
  width: 100%;
  height: 120px;
  object-fit: cover;
  object-position: 50% 80%;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.hl-b {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-3);
}

.hl-b b {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.hl-b span {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.hl-acts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-top: var(--space-2);
}

/* On the picture's corner, where people look to close a card. */
.hl-x {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
}

.hl-x:hover {
  background-image: var(--hover-neutral);
}
</style>
