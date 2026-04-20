import axios from 'axios';
import {computed, ref} from 'vue';

// Centralised authentication state
const isAuthenticated = ref(false);

// Share token state (set when app is loaded with ?token= query param)
let _shareToken = null;
const sessionContext = ref(null);
const isReadOnly = computed(() => sessionContext.value?.scope === 'READ');

function activateShareToken(token) {
  _shareToken = token;
}

const DEFAULT_BACKEND_PORT = 9537;
const environmentBaseUrl = import.meta?.env?.VITE_BACKEND_URL;
const API_PREFIX = '/api/v1';

function deriveBackendUrl() {
  if (environmentBaseUrl) return environmentBaseUrl;
  if (typeof window === 'undefined') {
    return `http://localhost:${DEFAULT_BACKEND_PORT}`;
  }
  const {protocol, hostname, port} = window.location;
  // The SPA is always served by the PixlStash server itself, so the backend
  // is always on the same origin as the page — regardless of port.
  const isStandardPort =
      (protocol === 'https:' && (port === '' || port === '443')) ||
      (protocol === 'http:' && (port === '' || port === '80'));
  if (isStandardPort) {
    return `${protocol}//${hostname}`;
  }
  return `${protocol}//${hostname}:${port}`;
}

const resolvedBaseUrl = deriveBackendUrl();
const apiBaseUrl = `${resolvedBaseUrl}${API_PREFIX}`;

// Axios instance
const apiClient = axios.create({
  baseURL: resolvedBaseUrl,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,  // Ensure cookies are included in requests
});

apiClient.interceptors.request.use((config) => {
  const rawUrl = config?.url;
  if (!rawUrl || typeof rawUrl !== 'string') {
    return config;
  }

  // Leave fully-qualified URLs untouched. Components that use API_BASE_URL
  // already include the /api/v1 path.
  if (/^https?:\/\//i.test(rawUrl)) {
    return config;
  }

  if (rawUrl.startsWith(API_PREFIX)) {
    return config;
  }

  config.url = rawUrl.startsWith('/')
      ? `${API_PREFIX}${rawUrl}`
      : `${API_PREFIX}/${rawUrl}`;

  if (_shareToken) {
    config.params = {...(config.params || {}), token: _shareToken};
  }

  return config;
});

// Login function
async function login(username, password) {
  try {
    const response = await apiClient.post('/login', {username, password});
    isAuthenticated.value = true;  // Update authentication state
    if (typeof window !== 'undefined' && 'credentials' in navigator &&
        'PasswordCredential' in window && username && password) {
      try {
        const credential = new PasswordCredential({
          id: username,
          name: username,
          password,
        });
        await navigator.credentials.store(credential);
      } catch {
        // Storing credentials is best-effort; ignore failures.
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
  activateShareToken,
  checkLoginStatus,
  checkSession,
  isAuthenticated,
  isReadOnly,
  login,
  logout,
  sessionContext,
  apiBaseUrl as API_BASE_URL,
};