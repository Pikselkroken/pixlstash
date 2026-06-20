<template>
  <div class="login-screen">
    <div v-if="tokenError" class="token-error-banner">
      <v-icon size="20" class="token-error-icon">mdi-link-off</v-icon>
      {{ tokenError }}
    </div>
    <h1 class="sr-only">
      {{ needsRegistration ? "Register for PixlStash" : "Log in to PixlStash" }}
    </h1>
    <div class="login-brand">
      <img src="/Logo.png" alt="" class="login-logo" />
      <WordmarkLogo class="login-wordmark" />
    </div>
    <p class="subtitle">
      {{
        needsRegistration
          ? "Set the login password. Minimum 8 characters."
          : "Type in your existing password to log in"
      }}
    </p>
    <form @submit.prevent="handleLogin" autocomplete="on">
      <div class="input-field">
        <input
          class="text-input"
          v-model="username"
          name="username"
          type="text"
          placeholder="Enter username"
          autocomplete="username"
        />
      </div>
      <div class="password-field">
        <input
          class="password-input"
          v-model="password"
          name="current-password"
          :type="showPassword ? 'text' : 'password'"
          placeholder="Enter password"
          autocomplete="current-password"
        />
        <button
          class="password-toggle"
          type="button"
          :aria-label="showPassword ? 'Hide password' : 'Show password'"
          @click="showPassword = !showPassword"
        >
          <v-icon size="18">{{
            showPassword ? "mdi-eye-off" : "mdi-eye"
          }}</v-icon>
        </button>
      </div>
      <button class="login-button" type="submit">
        {{ needsRegistration ? "Register" : "Login" }}
      </button>
      <p v-if="error" class="error">{{ error }}</p>
    </form>
    <p class="subtext">
      {{
        needsRegistration
          ? "Type in a new password to register and log in"
          : "If you've forgotten the login, start backend with --remove-password to reset"
      }}
    </p>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { checkLoginStatus, login } from "../../utils/apiClient";
import WordmarkLogo from "../WordmarkLogo.vue";

const props = defineProps({
  tokenError: { type: String, default: null },
});

const username = ref("");
const password = ref("");
const error = ref(null);
const needsRegistration = ref(false);
const showPassword = ref(false);

onMounted(async () => {
  try {
    const status = await checkLoginStatus();
    needsRegistration.value = status?.needs_registration;
  } catch (err) {
    console.error("Failed to load login status:", err);
  }
});

async function handleLogin() {
  try {
    error.value = null;
    await login(username.value, password.value); // Call the centralised login function
  } catch (err) {
    console.error("Login failed:", err);
    error.value = err.response?.data?.detail || err.message || "Login failed.";
  }
}
</script>

<style scoped>
.login-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: rgb(var(--v-theme-dark-surface));
  color-scheme: dark;
}

.login-brand {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-7);
}

.login-logo {
  width: 52px;
  height: 52px;
  object-fit: contain;
}

.login-wordmark {
  /* Tiny5 brand wordmark (WordmarkLogo.vue). */
  font-size: var(--text-2xl);
  color: rgb(var(--v-theme-on-dark-surface));
  --wordmark-accent: rgb(var(--v-theme-accent));
}

.token-error-banner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-6);
  padding: var(--space-4) var(--space-6);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-error), 0.15);
  border: 1px solid rgba(var(--v-theme-error), 0.5);
  color: rgb(var(--v-theme-error));
  font-size: var(--text-md);
  max-width: 360px;
  text-align: center;
}

.token-error-icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-error));
}

/* Visually hidden but exposed to assistive tech, so the login page keeps an
   <h1> document-outline landmark behind the logo + wordmark brand block. */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.subtitle {
  text-align: center;
  font-size: var(--text-lg);
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
}
.subtext {
  margin-top: var(--space-5);
  font-size: var(--text-base);
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
  text-align: center;
}
.text-input,
.password-input {
  padding: var(--space-3) var(--space-3) var(--space-3) var(--space-3);
  font-size: var(--text-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.3);
  border-radius: var(--radius-sm);
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
  width: 100%;
}

.input-field {
  display: flex;
  align-items: center;
}

.password-field {
  position: relative;
  display: flex;
  align-items: center;
}

.password-toggle {
  position: absolute;
  right: 0.5rem;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  cursor: pointer;
  font-size: var(--text-lg);
  line-height: 1;
  padding: var(--space-2);
}

.password-toggle:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: var(--radius-sm);
}

form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  padding: var(--space-7);
}

.login-button {
  padding: var(--space-3);
  font-size: var(--text-md);
  border: none;
  border-radius: var(--radius-sm);
  background-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  cursor: pointer;
}

.error {
  color: rgb(var(--v-theme-error));
}
</style>
