<template>
  <div class="login-screen">
    <form @submit.prevent="handleLogin">
      <input v-model="password" type="password" placeholder="Enter password" />
      <button type="submit">Login</button>
      <p v-if="error" class="error">{{ error }}</p>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { login } from '../utils/apiClient';

const password = ref('');
const error = ref(null);

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

form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.error {
  color: red;
}
</style>