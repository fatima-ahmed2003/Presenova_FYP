/**
 * API Service Module
 * Handles all HTTP requests to the Flask backend (http://localhost:5000)
 *
 * Key Features:
 * - JWT authentication with automatic token injection
 * - Centralized error handling
 * - 401 error handling (auto-logout on token expiry)
 * - Type-safe API responses
 */

import {
  AnalysisReport,
  SpeechMetrics,
  ChatResponse,
  ContextReport,
  ChatMessage,
  // ComparisonReport is no longer used (V1/V2 wizard removed)
  // ComparisonReport,
} from '../types';

export const API_BASE_URL = (
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  '/api'
).replace(/\/$/, '');

/**
 * ===== COLD START RESILIENT FETCH WRAPPER =====
 * Render free-tier instances sleep after inactivity and can take 30-50s to wake up.
 * During wake-up, HTTP requests may encounter network dropouts (502, 503, 504) or connection resets.
 * This wrapper automatically retries transient gateway errors with exponential backoff and a 60s timeout.
 */
export async function fetchWithColdStartRetry(
  url: string,
  options: RequestInit = {},
  retries = 2,
  backoffMs = 2500
): Promise<Response> {
  let attempt = 0;
  while (true) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 60000);
      const signal = options.signal || controller.signal;

      const response = await fetch(url, {
        ...options,
        signal,
      });

      clearTimeout(timeoutId);

      // Retry on Render cold-start gateway errors (502, 503, 504)
      if ([502, 503, 504].includes(response.status) && attempt < retries) {
        attempt++;
        const delay = backoffMs * attempt;
        console.warn(`[ColdStartRetry] Backend returned HTTP ${response.status} (server waking up). Retrying in ${delay}ms (${attempt}/${retries})...`);
        await new Promise((res) => setTimeout(res, delay));
        continue;
      }

      return response;
    } catch (err: any) {
      if (attempt < retries) {
        attempt++;
        const delay = backoffMs * attempt;
        console.warn(`[ColdStartRetry] Network error (${err?.message || 'timeout'}). Server may be spinning up. Retrying in ${delay}ms (${attempt}/${retries})...`);
        await new Promise((res) => setTimeout(res, delay));
        continue;
      }
      throw err;
    }
  }
}

/**
 * ===== JWT INTERCEPTOR =====
 * Helper function to fetch with automatic JWT token injection
 * Attaches Authorization header with Bearer token from localStorage
 *
 * @param url - API endpoint URL
 * @param options - Fetch options (method, body, headers, etc.)
 * @returns Response from the API
 *
 * CRITICAL: This function handles:
 * - Injecting JWT token from localStorage
 * - 401 Unauthorized errors (token expired/invalid)
 * - Automatic logout on auth failure
 */
async function fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
  // ===== STEP 1: GET TOKEN FROM LOCALSTORAGE =====
  const token = localStorage.getItem('auth_token');

  // ===== STEP 2: PREPARE REQUEST HEADERS =====
  const headers: Record<string, string> = {};
  
  if (options.headers) {
    if (options.headers instanceof Headers) {
      options.headers.forEach((value, key) => {
        headers[key] = value;
      });
    } else if (Array.isArray(options.headers)) {
      options.headers.forEach(([key, value]) => {
        headers[key] = value;
      });
    } else {
      Object.assign(headers, options.headers);
    }
  }

  // Only set Content-Type to application/json if not already set and body is not FormData
  if (!headers['Content-Type'] && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  // ===== STEP 3: INJECT JWT TOKEN IF EXISTS =====
  // CRITICAL: Token is automatically sent with every authenticated request
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  // ===== STEP 4: MAKE REQUEST WITH COLD-START RESILIENCE =====
  const response = await fetchWithColdStartRetry(url, {
    ...options,
    headers,
  });

  // ===== STEP 5: HANDLE 401 UNAUTHORIZED =====
  // If token is expired or invalid, clear auth state and redirect to login
  if (response.status === 401) {
    // Clear authentication
    localStorage.removeItem('auth_token');
    
    // Force logout by reloading page or triggering logout event
    // Frontend should listen for this and redirect to login
    window.dispatchEvent(new Event('auth-token-expired'));
    
    throw new Error('Your session has expired. Please login again.');
  }

  return response;
}

export { fetchWithAuth };

// ===== PHASE 1: AUTHENTICATION APIs =====

/**
 * User Signup
 * Creates a new user account
 *
 * @param name - User's name
 * @param email - User's email (must be unique)
 * @param password - User's password (min 6 characters)
 * @returns User object and JWT access token
 */
export interface SignupResponse {
  status: string;
  message: string;
  user: {
    id: string;
    name: string;
    email: string;
    created_at: string;
  };
  access_token: string;
  refresh_token?: string;
}

export const signup = async (
  name: string,
  email: string,
  password: string
): Promise<SignupResponse> => {
  try {
    const response = await fetchWithColdStartRetry(`${API_BASE_URL}/auth/signup`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ name, email, password }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || 'Signup failed');
    }

    return await response.json();
  } catch (error) {
    console.error('Error signing up:', error);
    throw error;
  }
};

/**
 * User Login
 * Authenticates user and returns JWT access token
 *
 * @param email - User's email
 * @param password - User's password
 * @returns User object and JWT access token
 */
export interface LoginResponse {
  status: string;
  message: string;
  user: {
    id: string;
    name: string;
    email: string;
    created_at: string;
  };
  access_token: string;
  refresh_token?: string;
}

export const login = async (email: string, password: string): Promise<LoginResponse> => {
  try {
    const response = await fetchWithColdStartRetry(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || 'Login failed');
    }

    return await response.json();
  } catch (error) {
    console.error('Error logging in:', error);
    throw error;
  }
};

/**
 * Firebase Identity Provider Login
 * Exchanges Firebase ID token for a server-issued Flask JWT access token.
 *
 * @param idToken - Firebase ID token string from client SDK
 * @returns User object and Flask JWT access token
 */
export const firebaseLogin = async (idToken: string): Promise<LoginResponse> => {
  try {
    const response = await fetchWithColdStartRetry(`${API_BASE_URL}/auth/firebase-login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ id_token: idToken }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || 'Firebase login verification failed');
    }

    return await response.json();
  } catch (error) {
    console.error('Error verifying Firebase ID token:', error);
    throw error;
  }
};

/**
 * Forgot Password Request
 * Requests a password reset token / email
 */
export interface ForgotPasswordResponse {
  success: boolean;
  status: string;
  message: string;
  dev_reset_token?: string;
}

export const forgotPassword = async (email: string): Promise<ForgotPasswordResponse> => {
  try {
    const response = await fetchWithColdStartRetry(`${API_BASE_URL}/auth/forgot-password`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.message || data.error || 'Failed to request password reset');
    }

    return data;
  } catch (error) {
    console.error('Error requesting password reset:', error);
    throw error;
  }
};

/**
 * Reset Password
 * Confirms new password using the reset token
 */
export interface ResetPasswordResponse {
  success: boolean;
  status: string;
  message: string;
}

export const resetPassword = async (token: string, password: string): Promise<ResetPasswordResponse> => {
  try {
    const response = await fetchWithColdStartRetry(`${API_BASE_URL}/auth/reset-password`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ token, password }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.message || data.error || 'Failed to reset password');
    }

    return data;
  } catch (error) {
    console.error('Error resetting password:', error);
    throw error;
  }
};

/**
 * Get Current User Profile
 * Requires valid JWT token
 *
 * @returns Current user object
 */
export interface UserResponse {
  status: string;
  user: {
    id: string;
    name: string;
    email: string;
    created_at: string;
  };
}

export const getCurrentUser = async (): Promise<UserResponse> => {
  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/auth/me`, {
      method: 'GET',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || 'Failed to fetch user profile');
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching current user:', error);
    throw error;
  }
};

// ===== PHASE 2: DOCUMENT ANALYSIS =====

/**
 * Phase 2: Send document file to backend for analysis
 */
export const analyzeDocument = async (file: File): Promise<AnalysisReport> => {
  const formData = new FormData();
  formData.append('file', file);

  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/analyze-document`, {
      method: 'POST',
      body: formData,
      headers: {
        // Don't set Content-Type for FormData (let browser set it with boundary)
      },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || `Document analysis failed: ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error analyzing document:', error);
    throw error;
  }
};

// ===== PHASE 4: SPEECH ANALYSIS =====

/**
 * Phase 4: Send transcribed speech to backend for analysis
 * 
 * @param text - The transcribed speech text
 * @param duration_seconds - Duration of the recording in seconds
 */
export const analyzeSpeech = async (text: string, duration_seconds: number): Promise<SpeechMetrics> => {
  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/analyze-speech`, {
      method: 'POST',
      body: JSON.stringify({ text, duration_seconds }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || `Speech analysis failed: ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error analyzing speech:', error);
    throw error;
  }
};

/**
 * Phase 3: Send audio file to backend for Whisper transcription & analysis
 */
export const analyzeAudio = async (file: File, duration_seconds: number): Promise<SpeechMetrics> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('duration_seconds', duration_seconds.toString());

  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/analyze-audio`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.message || err.error || 'Speech audio analysis failed');
    }

    return await response.json();
  } catch (error) {
    console.error('Error analyzing audio:', error);
    throw error;
  }
};

// ===== PHASE 5: AI COACH / PRACTICE MODE =====

/**
 * Phase 5: Send user message to AI coach for practice feedback
 */
export const sendChatMessage = async (
  message: string,
  history: ChatMessage[],
  contextReport: ContextReport
): Promise<ChatResponse> => {
  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/practice-chat`, {
      method: 'POST',
      body: JSON.stringify({
        message,
        history,
        contextReport,
      }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.message || err.error || `Chat request failed: ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error sending chat message:', error);
    throw error;
  }
};

export interface ReportItem {
  id: string;
  report_type: string;
  report_json: any;
  user_id: string;
  upload_id?: string;
  created_at: string;
}

export const getUserHistory = async (): Promise<{ status: string; reports: ReportItem[] }> => {
  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/auth/history`, {
      method: 'GET',
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.message || err.error || `Failed to fetch history: ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching user history:', error);
    throw error;
  }
};

// ===== (REMOVED) DOCUMENT COMPARISON =====
// compareDocuments() has been removed - V1/V2 wizard replaced by single-session analysis
// The /api/compare-documents endpoint is no longer called from the frontend

// ===== UTILITIES =====

/**
 * Health check to verify backend connectivity
 */
export const healthCheck = async (): Promise<boolean> => {
  try {
    const rootUrl = API_BASE_URL.replace(/\/api\/?$/, '') || window.location.origin;
    const response = await fetchWithColdStartRetry(`${rootUrl}/`, {
      method: 'GET',
    }, 1, 1000);
    return response.ok;
  } catch (error) {
    console.error('Backend health check failed:', error);
    return false;
  }
};

/**
 * Finalizes the live session and retrieves compiled feedback
 */
export const submitPresentationSession = async (sessionId: string): Promise<{ status: string; report: any }> => {
  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/presentation/submit`, {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.message || err.error || 'Failed to compile final live session report');
    }

    return await response.json();
  } catch (error) {
    console.error('Error in submitPresentationSession:', error);
    throw error;
  }
};

/**
 * Retrieves historical session scorecards for a specific topic
 */
export const getTopicHistory = async (topic: string): Promise<{ status: string; reports: any[] }> => {
  try {
    const response = await fetchWithAuth(`${API_BASE_URL}/presentation/history?topic=${encodeURIComponent(topic)}`, {
      method: 'GET',
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.message || err.error || 'Failed to retrieve topic history');
    }

    return await response.json();
  } catch (error) {
    console.error('Error in getTopicHistory:', error);
    throw error;
  }
};
