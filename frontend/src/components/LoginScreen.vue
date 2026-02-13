<template>
  <div class="login-screen">
    <h1 class="headline">
      {{
        needsRegistration
          ? "Register Password for PixlVault"
          : "Log In to PixlVault"
      }}
    </h1>
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
import { checkLoginStatus, login } from "../utils/apiClient";

const username = ref("");
const password = ref("");
const error = ref(null);
const needsRegistration = ref(false);
const showPassword = ref(false);

onMounted(async () => {
  try {
    const status = await checkLoginStatus();
    needsRegistration.value = status?.needs_registration;
    console.log("Login status:", status);
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

.headline {
  margin: 0 0 0.25rem;
  color: rgb(var(--v-theme-on-dark-surface));
  text-align: center;
}

.subtitle {
  text-align: center;
  font-size: 1.1rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
}
.subtext {
  margin-top: 1rem;
  font-size: 0.9rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
  text-align: center;
}
.text-input,
.password-input {
  padding: 0.5rem 0.5rem 0.5rem 0.5rem;
  font-size: 1rem;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.3);
  border-radius: 4px;
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
  color: #ccc;
  cursor: pointer;
  font-size: 1.1rem;
  line-height: 1;
  padding: 0.25rem;
}

.password-toggle:focus-visible {
  outline: 2px solid #888;
  border-radius: 4px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  color: #ccc;
  padding: 2rem;
}

.login-button {
  padding: 0.5rem;
  font-size: 1rem;
  border: none;
  border-radius: 4px;
  background-color: orange;
  color: #000;
  cursor: pointer;
}

.error {
  color: red;
}
</style>
