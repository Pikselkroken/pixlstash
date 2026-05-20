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
  apiClient,
  checkSession,
  isAuthenticated,
  sessionContext,
} from "./utils/apiClient";
import LoginScreen from "./components/views/LoginScreen.vue";

const isChecking = ref(true);
const tokenError = ref(null);

onMounted(async () => {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (token) {
    activateShareToken(token);
    try {
      const ctx = await apiClient.get("/session/context");
      sessionContext.value = ctx.data;
      isAuthenticated.value = true;
      isChecking.value = false;
      return;
    } catch {
      // Invalid token — show login screen with error
      tokenError.value = "The share link is invalid or has expired.";
      isChecking.value = false;
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
});
</script>

<style scoped>
.root-loading {
  height: 100vh;
  background: rgb(var(--v-theme-dark-surface, 18 18 18));
}
</style>
