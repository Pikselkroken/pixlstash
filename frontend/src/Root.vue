<template>
  <div>
    <LoginScreen v-if="!isAuthenticated" />
    <App v-else />
  </div>
</template>

<script setup>
import { onMounted } from "vue";
import { activateShareToken, apiClient, checkSession, isAuthenticated, sessionContext } from "./utils/apiClient";
import LoginScreen from "./components/LoginScreen.vue";
import App from "./App.vue";

onMounted(async () => {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (token) {
    activateShareToken(token);
    try {
      const ctx = await apiClient.get("/session/context");
      sessionContext.value = ctx.data;
      isAuthenticated.value = true;
      return;
    } catch {
      // Invalid token — fall through to normal login flow
    }
  }
  const session = await checkSession();
  if (session?.status === "ok") {
    isAuthenticated.value = true;
  } else if (session?.status === "invalid") {
    isAuthenticated.value = false;
  }
});
</script>
