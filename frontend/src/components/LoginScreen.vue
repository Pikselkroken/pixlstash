<template>
  <div class="login-screen">
    <h1 class="headline">{{ needsRegistration ? 'Register' : 'Log In' }}</h1>
    <p class="subtitle">
      {{
        needsRegistration
          ? 'Set the login password'
          : "If you've forgotten the login, start the backend with --remove-password to reset"
      }}
    </p>
    <form @submit.prevent="handleLogin">
      <input v-model="password" type="password" placeholder="Enter password" />
      <button type="submit">Login</button>
      <p v-if="error" class="error">{{ error }}</p>
    </form>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue';
import { checkLoginStatus, login } from '../utils/apiClient';

const password = ref('');
const error = ref(null);
const needsRegistration = ref(false);

onMounted(async () => {
  try {
    const status = await checkLoginStatus();
    needsRegistration.value = !!status?.needs_registration;
  } catch (err) {
    console.error('Failed to load login status:', err);
  }
});

async function handleLogin() {
  try {
    error.value = null;
    await login(password.value); // Call the centralized login function
  } catch (err) {
    console.error('Login failed:', err);
    error.value = 'Login failed. Please try again.';
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
}

.headline {
  margin: 0 0 0.25rem;
}

.subtitle {
  margin: 0 0 1.5rem;
  text-align: center;
  max-width: 28rem;
  font-size: 0.9rem;
  color: #666;
}

form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.error {
  color: red;
}
</style>