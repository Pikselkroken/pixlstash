<template>
  <div>
    <LoginScreen v-if="!isAuthenticated" />
    <App v-else />
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { checkSession, isAuthenticated } from "./utils/apiClient";
import LoginScreen from "./components/LoginScreen.vue";
import App from "./App.vue";

onMounted(async () => {
  const session = await checkSession();
  if (session?.status === "ok") {
    isAuthenticated.value = true;
  } else if (session?.status === "invalid") {
    isAuthenticated.value = false;
  }
});
</script>
