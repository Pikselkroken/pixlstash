import axios from 'axios';
import {ref} from 'vue';

// Centralised authentication state
const isAuthenticated = ref(false);

const DEFAULT_BACKEND_PORT = 9537;
const environmentBaseUrl = import.meta?.env?.VITE_BACKEND_URL;
const browserHost =
    typeof window !== 'undefined' ? window.location.hostname : 'localhost';
const resolvedBaseUrl =
    environmentBaseUrl || `http://${browserHost}:${DEFAULT_BACKEND_PORT}`;

// Axios instance
const apiClient = axios.create({
  baseURL: resolvedBaseUrl,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,  // Ensure cookies are included in requests
});

// Login function
async function login(username, password) {
  try {
    const response = await apiClient.post('/login', {username, password});
    isAuthenticated.value = true;  // Update authentication state
    console.log('Login successful:', response.data);
    if (typeof window !== 'undefined' && 'credentials' in navigator &&
        'PasswordCredential' in window && username && password) {
      try {
        const credential = new PasswordCredential({
          id: username,
          name: username,
          password,
        });
        await navigator.credentials.store(credential);
      } catch (credentialError) {
        console.debug('Credential store failed:', credentialError);
      }
    }
    return response.data;  // Return response data for further use if needed
  } catch (error) {
    console.error('Login failed:', error);
    throw error;  // Re-throw the error for the caller to handle
  }
}

// Logout function
async function logout() {
  try {
    await apiClient.post('/logout');
    console.log('User logged out successfully.');
  } catch (error) {
    console.error('Logout failed:', error);
  }
  isAuthenticated.value = false;  // Update authentication state}
}

// Check session function
async function checkSession() {
  try {
    const response = await apiClient.get('/check-session');
    isAuthenticated.value = true;  // Update authentication state
    console.log('Session valid:', response.data);
    return {status: 'ok', data: response.data};
  } catch (error) {
    if (error.response && error.response.status === 401) {
      console.warn('Session invalid or expired:', error);
      isAuthenticated.value = false;  // Update authentication state
      return {status: 'invalid'};
    }
    console.warn('Backend unreachable while checking session:', error);
    return {status: 'unreachable'};
  }
}

// Check if registration is required
async function checkLoginStatus() {
  try {
    const response = await apiClient.get('/login');
    return response.data;
  } catch (error) {
    console.error('Login status check failed:', error);
    throw error;
  }
}

// Interceptor to handle 401 errors globally
apiClient.interceptors.response.use((response) => response, (error) => {
  if (error.response && error.response.status === 401) {
    const url = error?.config?.url || '';
    if (!url.includes('/users/me/auth')) {
      console.error('Unauthorised! Logging out...');
      logout();  // Call the centralised logout function
    }
  }
  return Promise.reject(error);
});

export {
  apiClient,
  checkLoginStatus,
  checkSession,
  isAuthenticated,
  login,
  logout,
  resolvedBaseUrl as API_BASE_URL,
};