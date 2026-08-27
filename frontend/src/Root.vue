<template>
  <div>
    <div v-if="isChecking" class="root-loading" />
    <template v-else>
      <LoginScreen v-if="!isAuthenticated" :tokenError="tokenError" />
      <RouterView v-else />
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { RouterView } from "vue-router";
import {
  activateShareToken,
  checkSession,
  isAuthenticated,
  sessionContext,
} from "./utils/apiClient";
import { getSessionContext } from "./api/session";
import LoginScreen from "./components/views/LoginScreen.vue";
import { markEnd, markStart } from "./utils/perfMarks";

const isChecking = ref(true);
const tokenError = ref(null);

onMounted(async () => {
  // This gate blocks App.vue from rendering at all until it resolves, so its
  // cost is pure "blank screen" time - see the boot-breakdown report.
  markStart("pixlstash:auth-check");
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (token) {
    activateShareToken(token);
    try {
      sessionContext.value = await getSessionContext();
      isAuthenticated.value = true;
      isChecking.value = false;
      markEnd("pixlstash:auth-check");
      return;
    } catch {
      // Invalid token - show login screen with error
      tokenError.value = "The share link is invalid or has expired.";
      isChecking.value = false;
      markEnd("pixlstash:auth-check");
      return;
    }
  }
  const session = await checkSession();
  if (session?.status === "ok") {
    isAuthenticated.value = true;
  } else if (session?.status === "invalid") {
    isAuthenticated.value = false;
  }
  isChecking.value = false;
  markEnd("pixlstash:auth-check");
});
</script>

<style scoped>
.root-loading {
  height: 100vh;
  background: rgb(var(--v-theme-dark-surface, 18 18 18));
}
</style>
