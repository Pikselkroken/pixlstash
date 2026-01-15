import axios from 'axios';
import { ref } from 'vue';

// Centralized authentication state
const isAuthenticated = ref(false);

// Axios instance
const apiClient = axios.create({
  baseURL: 'http://localhost:9537',
  timeout: 60000, // Increased timeout to 60 seconds
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true, // Ensure cookies are included in requests
});

// Login function
async function login(password) {
  try {
    const response = await apiClient.post('/login', { password });
    isAuthenticated.value = true; // Update authentication state
    console.log('Login successful:', response.data);
    return response.data; // Return response data for further use if needed
  } catch (error) {
    console.error('Login failed:', error);
    throw error; // Re-throw the error for the caller to handle
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
  isAuthenticated.value = false; // Update authentication state}
}

// Check session function
async function checkSession() {
  try {
    const response = await apiClient.get('/check-session');
    isAuthenticated.value = true; // Update authentication state
    console.log('Session valid:', response.data);
    return response.data; // Return user data if needed
  } catch (error) {
    console.warn('Session invalid or expired:', error);
    isAuthenticated.value = false; // Update authentication state
    return null; // Return null to indicate no valid session
  }
}

// Interceptor to handle 401 errors globally
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      console.error('Unauthorized! Logging out...');
      logout(); // Call the centralized logout function
    }
    return Promise.reject(error);
  }
);

export { apiClient, checkSession, isAuthenticated, login, logout };