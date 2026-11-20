import { initializeApp, getApps } from 'firebase/app';
import {
  getAuth,
  GoogleAuthProvider,
} from 'firebase/auth';

type NativeGoogleBridge = {
  postMessage: (message: string) => void;
  addEventListener: (type: 'message', listener: (event: MessageEvent<string>) => void) => void;
  removeEventListener: (type: 'message', listener: (event: MessageEvent<string>) => void) => void;
};

export const hasNativeGoogleSignIn = (): boolean => {
  return typeof window !== 'undefined' && Boolean((window as any).PresenovaAuth);
};

export const requestNativeGoogleIdToken = (): Promise<string> => {
  return new Promise((resolve, reject) => {
    const bridge = (window as any).PresenovaAuth as NativeGoogleBridge | undefined;
    if (!bridge || typeof bridge.postMessage !== 'function') {
      reject(Object.assign(new Error('Native Google sign-in is unavailable.'), { code: 'native/unavailable' }));
      return;
    }

    let settled = false;
    let timeout = 0;
    const cleanup = () => {
      window.clearTimeout(timeout);
      bridge.removeEventListener('message', onMessage);
    };
    const fail = (code: string) => {
      if (settled) return;
      settled = true;
      cleanup();
      const messages: Record<string, string> = {
        cancelled: 'Google sign-in was cancelled.',
        timeout: 'Google sign-in timed out. Please try again.',
        configuration: 'Google sign-in is not configured for this Android app yet.',
        credential: 'Google did not return a valid sign-in credential.',
        'in-progress': 'A Google sign-in is already in progress.',
        unavailable: 'Google sign-in is temporarily unavailable. Please try again.',
      };
      reject(Object.assign(new Error(messages[code] || messages.unavailable), { code: `native/${code}` }));
    };
    const onMessage = (event: MessageEvent<string>) => {
      let response: { type?: unknown; code?: unknown; idToken?: unknown };
      try { response = JSON.parse(event.data); } catch { return; }
      if (response.type === 'error' && typeof response.code === 'string') {
        fail(response.code);
      } else if (response.type === 'success' && typeof response.idToken === 'string' && response.idToken.length > 0 && response.idToken.length <= 16384) {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(response.idToken);
      } else {
        fail('credential');
      }
    };
    timeout = window.setTimeout(() => fail('timeout'), 65_000);
    bridge.addEventListener('message', onMessage);
    try { bridge.postMessage('google-sign-in'); }
    catch { fail('unavailable'); }
  });
};

// FIX-4 (AGENTS.md §2): Never hardcode Firebase project credentials as fallbacks —
// they leak project IDs into the production JS bundle even when minified.
// Use isFirebaseConfigured() guard before calling signInWithPopup / signInWithRedirect.
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY || '',
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || '',
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID || '',
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET || '',
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID || '',
  appId: import.meta.env.VITE_FIREBASE_APP_ID || '',
};

const app = !getApps().length ? initializeApp(firebaseConfig) : getApps()[0];
export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();

export const sendFirebasePasswordReset = async (email: string): Promise<void> => {
  const authModule = await import('firebase/auth');
  const sendEmailFn = (authModule as any).sendPasswordResetEmail;
  if (typeof sendEmailFn === 'function') {
    await sendEmailFn(auth, email);
  } else {
    throw new Error('Firebase password reset function is unavailable.');
  }
};

export const isElectron = (): boolean => {
  return (
    typeof window !== 'undefined' &&
    (navigator.userAgent.toLowerCase().includes('electron') || !!(window as any).electron)
  );
};

export const isFirebaseConfigured = (): boolean => {
  const key = import.meta.env.VITE_FIREBASE_API_KEY;
  return Boolean(
    key &&
    key.trim() !== '' &&
    key !== 'your-firebase-api-key' &&
    key !== 'AIzaSyDemoKeyPlaceholder' &&
    !key.includes('Placeholder') &&
    !key.includes('your-') &&
    key.length > 20
  );
};

/**
 * Maps raw Firebase auth error codes into friendly human-readable error messages.
 */
export const getFirebaseErrorMessage = (error: any): string => {
  const code = error?.code || '';
  const message = error?.message || '';
  if (code === 'auth/api-key-not-valid' || code === 'auth/invalid-api-key' || message.includes('api-key-not-valid')) {
    return 'Firebase Web API Key is not configured on Render. Please use the Email & Password form below (Sign Up / Log In) to access your account.';
  }
  switch (code) {
    case 'auth/invalid-email':
      return 'Please enter a valid email address.';
    case 'auth/user-disabled':
      return 'This user account has been disabled.';
    case 'auth/user-not-found':
      return 'No account found with this email. Please sign up first.';
    case 'auth/wrong-password':
    case 'auth/invalid-credential':
      return 'Incorrect email or password. Please try again.';
    case 'auth/email-already-in-use':
      return 'An account with this email already exists. Please log in instead.';
    case 'auth/weak-password':
      return 'Password should be at least 6 characters long.';
    case 'auth/popup-closed-by-user':
      return 'Sign-in popup was closed before completing authentication.';
    case 'auth/popup-blocked':
      return 'Sign-in popup was blocked by browser. Please allow popups for this site.';
    case 'auth/network-request-failed':
      return 'Network connection error. Please check your connection and try again.';
    case 'auth/unauthorized-domain':
      return `This domain (${typeof window !== 'undefined' ? window.location.hostname : 'your domain'}) is not authorized in Firebase Console. Please add "${typeof window !== 'undefined' ? window.location.hostname : 'fyp-presenova-frontend.onrender.com'}" to Firebase Console > Authentication > Settings > Authorized domains.`;
    case 'auth/configuration-not-found':
    case 'auth/operation-not-allowed':
      return 'Firebase Authentication is not enabled in Firebase Console. Go to Firebase Console > Authentication > Sign-in method and enable Google / Email-Password.';
    default:
      return error?.message || 'Authentication failed. Please try again.';
  }
};
