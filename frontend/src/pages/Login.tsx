/**
 * Login Page (Phase 1 / Firebase Identity Provider Integration)
 * Unified Firebase Authentication (Email/Password + Google Sign-In)
 *
 * Flow:
 * 1. User signs in with Google or Email/Password via Firebase SDK.
 * 2. Client extracts Firebase ID Token (user.getIdToken()).
 * 3. Client sends ID Token to Flask backend (/api/auth/firebase-login).
 * 4. Backend verifies token, creates/syncs Firestore user, and issues a Flask JWT.
 * 5. Client stores the Flask JWT for all subsequent API authorization.
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  firebaseLogin, 
  login as flaskLogin, 
  signup as flaskSignup,
  forgotPassword as flaskForgotPassword,
  resetPassword as flaskResetPassword 
} from '../services/api';
import { useAuth } from '../context/AuthContext';
import { 
  auth, 
  googleProvider, 
  getFirebaseErrorMessage, 
  isElectron, 
  isFirebaseConfigured,
  sendFirebasePasswordReset
} from '../services/firebase';
import { 
  signInWithPopup, 
  signInWithRedirect,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  updateProfile
} from 'firebase/auth';
import './Login.css';

type FormTab = 'login' | 'signup' | 'forgot-password';

const Login: React.FC = () => {
  const navigate = useNavigate();
  const { login: loginContext, isAuthenticated } = useAuth();

  // Redirect to Dashboard if already authenticated
  React.useEffect(() => {
    if (isAuthenticated) {
      navigate('/analytics');
    }
  }, [isAuthenticated, navigate]);

  // ===== UI STATE MANAGEMENT =====
  const [activeTab, setActiveTab] = useState<FormTab>('login');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // ===== LOGIN FORM STATE =====
  const [loginForm, setLoginForm] = useState({
    email: '',
    password: '',
  });

  // ===== SIGNUP FORM STATE =====
  const [signupForm, setSignupForm] = useState({
    name: '',
    email: '',
    password: '',
    confirmPassword: '',
  });

  // ===== FORGOT / RESET PASSWORD FORM STATE =====
  const [resetEmail, setResetEmail] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [showTokenSection, setShowTokenSection] = useState(false);

  // ===== PASSWORD VISIBILITY TOGGLE STATES =====
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [showSignupPassword, setShowSignupPassword] = useState(false);
  const [showSignupConfirmPassword, setShowSignupConfirmPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmNewPassword, setShowConfirmNewPassword] = useState(false);

  /**
   * Helper: Sends Firebase ID token to Flask backend to complete authentication
   */
  const handleFirebaseTokenExchange = async (firebaseUser: any) => {
    try {
      setMessage('Verifying session with Presenova server...');
      const idToken = await firebaseUser.getIdToken(true);
      const response = await firebaseLogin(idToken);

      // Save Flask JWT in AuthContext
      loginContext(response.user, response.access_token, response.refresh_token);
      setMessage('Authentication successful! Redirecting to Dashboard...');

      setTimeout(() => {
        navigate('/analytics');
      }, 800);
    } catch (err: any) {
      console.error('Firebase token exchange failed:', err);
      const errMsg = err?.message || 'Server verification failed. Please try again.';
      setError(errMsg);
    }
  };

  /**
   * Handle Google Sign-In
   * Uses popup for Web browser and redirect fallback for Electron runtime
   */
  const handleGoogleSignIn = async () => {
    setError(null);
    setMessage(null);
    setIsLoading(true);

    if (!isFirebaseConfigured()) {
      setError('Firebase Web API Key is not configured on Render yet. Please use the Email & Password form below (Sign Up / Log In) to access your account.');
      setIsLoading(false);
      return;
    }

    try {
      let userCredential;
      if (isElectron()) {
        await signInWithRedirect(auth, googleProvider);
        return;
      } else {
        userCredential = await signInWithPopup(auth, googleProvider);
      }

      if (userCredential?.user) {
        await handleFirebaseTokenExchange(userCredential.user);
      }
    } catch (err: any) {
      console.error('Google sign-in error:', err);
      setError(getFirebaseErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Handle Email/Password Login (via Firebase Auth SDK with Backend Fallback)
   */
  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setIsLoading(true);

    if (!loginForm.email || !loginForm.password) {
      setError('Please fill in both email and password');
      setIsLoading(false);
      return;
    }

    try {
      if (isFirebaseConfigured()) {
        try {
          const userCredential = await signInWithEmailAndPassword(auth, loginForm.email.trim(), loginForm.password);
          if (userCredential?.user) {
            await handleFirebaseTokenExchange(userCredential.user);
            return;
          }
        } catch (fbErr: any) {
          console.warn('Firebase email login attempt:', fbErr);
          // If account was created locally or on Flask backend, check fallback
          if (
            fbErr?.code === 'auth/user-not-found' || 
            fbErr?.code === 'auth/invalid-credential' || 
            fbErr?.code === 'auth/wrong-password' ||
            fbErr?.code === 'auth/operation-not-allowed'
          ) {
            try {
              const response = await flaskLogin(loginForm.email.trim(), loginForm.password);
              loginContext(response.user, response.access_token, response.refresh_token);
              setMessage('Login successful! Redirecting to Dashboard...');
              setTimeout(() => navigate('/analytics'), 800);
              return;
            } catch (flaskErr: any) {
              setError(getFirebaseErrorMessage(fbErr));
              return;
            }
          } else {
            setError(getFirebaseErrorMessage(fbErr));
            return;
          }
        }
      }

      // Direct Flask Login fallback
      const response = await flaskLogin(loginForm.email.trim(), loginForm.password);
      loginContext(response.user, response.access_token, response.refresh_token);
      setMessage('Login successful! Redirecting to Dashboard...');
      setTimeout(() => navigate('/analytics'), 800);
    } catch (flaskErr: any) {
      console.error('Flask backend login error:', flaskErr);
      setError(flaskErr?.message || 'Login failed. Incorrect email or password, or account not registered yet.');
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Handle Email/Password Registration (via Firebase Auth SDK with Backend Fallback)
   */
  const handleEmailSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setIsLoading(true);

    if (!signupForm.name || !signupForm.email || !signupForm.password) {
      setError('Please fill in all required fields');
      setIsLoading(false);
      return;
    }

    if (signupForm.password !== signupForm.confirmPassword) {
      setError('Passwords do not match');
      setIsLoading(false);
      return;
    }

    if (signupForm.password.length < 8) {
      setError('Password must be at least 8 characters long');
      setIsLoading(false);
      return;
    }

    try {
      if (isFirebaseConfigured()) {
        try {
          const userCredential = await createUserWithEmailAndPassword(auth, signupForm.email.trim(), signupForm.password);
          if (userCredential?.user) {
            if (signupForm.name.trim()) {
              try {
                await updateProfile(userCredential.user, { displayName: signupForm.name.trim() });
              } catch (profErr) {
                console.warn('Could not set displayName on Firebase user:', profErr);
              }
            }
            await handleFirebaseTokenExchange(userCredential.user);
            return;
          }
        } catch (fbErr: any) {
          console.warn('Firebase email signup attempt failed:', fbErr);
          if (fbErr?.code === 'auth/operation-not-allowed') {
            const response = await flaskSignup(signupForm.name.trim(), signupForm.email.trim(), signupForm.password);
            loginContext(response.user, response.access_token, response.refresh_token);
            setMessage('Account created successfully! Redirecting to Dashboard...');
            setTimeout(() => navigate('/analytics'), 800);
            return;
          } else {
            setError(getFirebaseErrorMessage(fbErr));
            return;
          }
        }
      }

      // Direct Flask Signup fallback
      const response = await flaskSignup(signupForm.name.trim(), signupForm.email.trim(), signupForm.password);
      loginContext(response.user, response.access_token, response.refresh_token);
      setMessage('Account created successfully! Redirecting to Dashboard...');
      setTimeout(() => navigate('/analytics'), 800);
    } catch (flaskErr: any) {
      console.error('Flask backend signup error:', flaskErr);
      setError(flaskErr?.message || 'Registration failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Handle Requesting Password Reset (Forgot Password)
   */
  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setIsLoading(true);

    const emailToReset = resetEmail.trim();
    if (!emailToReset) {
      setError('Please provide your email address.');
      setIsLoading(false);
      return;
    }

    try {
      if (isFirebaseConfigured()) {
        try {
          await sendFirebasePasswordReset(emailToReset);
        } catch (fbErr: any) {
          console.warn('Firebase password reset email attempt:', fbErr);
        }
      }

      // Backend API call
      const backendResp = await flaskForgotPassword(emailToReset);
      
      let successMsg = backendResp?.message || 'If an account with this email exists, password reset instructions have been generated.';
      if (backendResp?.dev_reset_token) {
        setResetToken(backendResp.dev_reset_token);
        setShowTokenSection(true);
        successMsg += ' (Dev token automatically filled below for quick testing)';
      } else {
        setShowTokenSection(true);
      }
      setMessage(successMsg);
    } catch (err: any) {
      setError(err?.message || 'Could not process password reset request. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Handle Reset Password Confirmation with Token
   */
  const handleResetPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setIsLoading(true);

    if (!resetToken.trim()) {
      setError('Please enter the reset token.');
      setIsLoading(false);
      return;
    }

    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters long.');
      setIsLoading(false);
      return;
    }

    if (newPassword !== confirmNewPassword) {
      setError('Passwords do not match.');
      setIsLoading(false);
      return;
    }

    try {
      const resp = await flaskResetPassword(resetToken.trim(), newPassword);
      setMessage(resp?.message || 'Password successfully updated! Redirecting to login...');
      setTimeout(() => {
        setActiveTab('login');
        setLoginForm((prev) => ({ ...prev, email: resetEmail.trim(), password: '' }));
        setShowTokenSection(false);
        setResetToken('');
        setNewPassword('');
        setConfirmNewPassword('');
        setMessage('Password updated! You can now log in.');
      }, 1500);
    } catch (err: any) {
      setError(err?.message || 'Password reset failed. Token may be invalid or expired.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        <div className="login-card">
          {/* Header */}
          <div className="login-header">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '12px' }}>
  <img src="/logo-icon.png" alt="Presenova logo" style={{ height: 40, width: 'auto' }} />
  <h1 style={{ margin: 0 }}>Presenova</h1>
  </div>
            <p>Presentation Coaching & Analysis Ecosystem</p>
          </div>

          {/* Tabs */}
          <div className="login-tabs">
            <button
              className={`tab-button ${activeTab === 'login' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('login');
                setError(null);
                setMessage(null);
              }}
              disabled={isLoading}
            >
              Log In
            </button>
            <button
              className={`tab-button ${activeTab === 'signup' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('signup');
                setError(null);
                setMessage(null);
              }}
              disabled={isLoading}
            >
              Sign Up
            </button>
            {activeTab === 'forgot-password' && (
              <button
                className="tab-button active"
                disabled={isLoading}
              >
                Reset Password
              </button>
            )}
          </div>

          {/* Feedback Messages */}
          {error && <div className="message message-error">{error}</div>}
          {message && <div className="message message-success">{message}</div>}

          <div className="login-form">
            {/* Google One-Click Sign In (Only for Login & Signup) */}
            {activeTab !== 'forgot-password' && (
              <>
                <button
                  type="button"
                  className="google-auth-btn"
                  onClick={handleGoogleSignIn}
                  disabled={isLoading}
                >
                  <svg className="google-icon" viewBox="0 0 24 24">
                    <path
                      fill="#4285F4"
                      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                    />
                    <path
                      fill="#34A853"
                      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                    />
                    <path
                      fill="#FBBC05"
                      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                    />
                    <path
                      fill="#EA4335"
                      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                    />
                  </svg>
                  <span>Continue with Google</span>
                </button>

                <div className="auth-divider">
                  <span>Or with Email</span>
                </div>
              </>
            )}

            {/* Email/Password Login Form */}
            {activeTab === 'login' && (
              <form onSubmit={handleEmailLogin}>
                <div className="form-group">
                  <label htmlFor="login-email">Email Address</label>
                  <input
                    id="login-email"
                    type="email"
                    placeholder="your@email.com"
                    value={loginForm.email}
                    onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })}
                    disabled={isLoading}
                    required
                  />
                </div>

                <div className="form-group">
                  <div className="label-row">
                    <label htmlFor="login-password">Password</label>
                    <button
                      type="button"
                      className="forgot-password-link"
                      onClick={() => {
                        setActiveTab('forgot-password');
                        setResetEmail(loginForm.email);
                        setError(null);
                        setMessage(null);
                      }}
                      disabled={isLoading}
                    >
                      Forgot password?
                    </button>
                  </div>
                  <div className="password-input-wrapper">
                    <input
                      id="login-password"
                      type={showLoginPassword ? 'text' : 'password'}
                      placeholder="Enter your password"
                      value={loginForm.password}
                      onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })}
                      disabled={isLoading}
                      required
                    />
                    <button
                      type="button"
                      className="password-toggle-btn"
                      onClick={() => setShowLoginPassword((prev) => !prev)}
                      aria-label={showLoginPassword ? 'Hide password' : 'Show password'}
                      tabIndex={0}
                    >
                      {showLoginPassword ? (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                          <line x1="1" y1="1" x2="23" y2="23" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                          <circle cx="12" cy="12" r="3" />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>

                <button type="submit" className="login-button" disabled={isLoading}>
                  {isLoading ? 'Signing in...' : 'Log In'}
                </button>
              </form>
            )}

            {/* Email/Password Signup Form */}
            {activeTab === 'signup' && (
              <form onSubmit={handleEmailSignup}>
                <div className="form-group">
                  <label htmlFor="signup-name">Full Name</label>
                  <input
                    id="signup-name"
                    type="text"
                    placeholder="John Doe"
                    value={signupForm.name}
                    onChange={(e) => setSignupForm({ ...signupForm, name: e.target.value })}
                    disabled={isLoading}
                    required
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="signup-email">Email Address</label>
                  <input
                    id="signup-email"
                    type="email"
                    placeholder="your@email.com"
                    value={signupForm.email}
                    onChange={(e) => setSignupForm({ ...signupForm, email: e.target.value })}
                    disabled={isLoading}
                    required
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="signup-password">Password (Min 8 characters)</label>
                  <div className="password-input-wrapper">
                    <input
                      id="signup-password"
                      type={showSignupPassword ? 'text' : 'password'}
                      placeholder="At least 8 characters"
                      value={signupForm.password}
                      onChange={(e) => setSignupForm({ ...signupForm, password: e.target.value })}
                      disabled={isLoading}
                      required
                    />
                    <button
                      type="button"
                      className="password-toggle-btn"
                      onClick={() => setShowSignupPassword((prev) => !prev)}
                      aria-label={showSignupPassword ? 'Hide password' : 'Show password'}
                      tabIndex={0}
                    >
                      {showSignupPassword ? (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                          <line x1="1" y1="1" x2="23" y2="23" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                          <circle cx="12" cy="12" r="3" />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>

                <div className="form-group">
                  <label htmlFor="signup-confirm">Confirm Password</label>
                  <div className="password-input-wrapper">
                    <input
                      id="signup-confirm"
                      type={showSignupConfirmPassword ? 'text' : 'password'}
                      placeholder="Confirm your password"
                      value={signupForm.confirmPassword}
                      onChange={(e) => setSignupForm({ ...signupForm, confirmPassword: e.target.value })}
                      disabled={isLoading}
                      required
                    />
                    <button
                      type="button"
                      className="password-toggle-btn"
                      onClick={() => setShowSignupConfirmPassword((prev) => !prev)}
                      aria-label={showSignupConfirmPassword ? 'Hide password' : 'Show password'}
                      tabIndex={0}
                    >
                      {showSignupConfirmPassword ? (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                          <line x1="1" y1="1" x2="23" y2="23" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                          <circle cx="12" cy="12" r="3" />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>

                <button type="submit" className="login-button" disabled={isLoading}>
                  {isLoading ? 'Creating account...' : 'Create Account'}
                </button>
              </form>
            )}

            {/* Forgot / Reset Password View */}
            {activeTab === 'forgot-password' && (
              <div className="forgot-password-flow">
                <p className="forgot-password-desc">
                  Enter your registered account email to request password reset instructions.
                </p>

                <form onSubmit={handleForgotPassword} className="forgot-email-form">
                  <div className="form-group">
                    <label htmlFor="reset-email">Account Email</label>
                    <input
                      id="reset-email"
                      type="email"
                      placeholder="your@email.com"
                      value={resetEmail}
                      onChange={(e) => setResetEmail(e.target.value)}
                      disabled={isLoading}
                      required
                    />
                  </div>

                  <button type="submit" className="login-button" disabled={isLoading}>
                    {isLoading ? 'Sending instructions...' : 'Send Reset Instructions'}
                  </button>
                </form>

                <div className="token-toggle-section">
                  <button
                    type="button"
                    className="toggle-token-btn"
                    onClick={() => setShowTokenSection(!showTokenSection)}
                  >
                    {showTokenSection ? '▲ Hide token input' : '▼ Have a reset token? Enter here'}
                  </button>
                </div>

                {showTokenSection && (
                  <form onSubmit={handleResetPasswordSubmit} className="reset-token-form">
                    <div className="form-group">
                      <label htmlFor="reset-token">Reset Token</label>
                      <input
                        id="reset-token"
                        type="text"
                        placeholder="Paste reset token here"
                        value={resetToken}
                        onChange={(e) => setResetToken(e.target.value)}
                        disabled={isLoading}
                        required
                      />
                    </div>

                    <div className="form-group">
                      <label htmlFor="new-password">New Password (Min 8 characters)</label>
                      <div className="password-input-wrapper">
                        <input
                          id="new-password"
                          type={showNewPassword ? 'text' : 'password'}
                          placeholder="At least 8 characters"
                          value={newPassword}
                          onChange={(e) => setNewPassword(e.target.value)}
                          disabled={isLoading}
                          required
                        />
                        <button
                          type="button"
                          className="password-toggle-btn"
                          onClick={() => setShowNewPassword((prev) => !prev)}
                          aria-label={showNewPassword ? 'Hide password' : 'Show password'}
                          tabIndex={0}
                        >
                          {showNewPassword ? (
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                              <line x1="1" y1="1" x2="23" y2="23" />
                            </svg>
                          ) : (
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                              <circle cx="12" cy="12" r="3" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>

                    <div className="form-group">
                      <label htmlFor="confirm-new-password">Confirm New Password</label>
                      <div className="password-input-wrapper">
                        <input
                          id="confirm-new-password"
                          type={showConfirmNewPassword ? 'text' : 'password'}
                          placeholder="Confirm your new password"
                          value={confirmNewPassword}
                          onChange={(e) => setConfirmNewPassword(e.target.value)}
                          disabled={isLoading}
                          required
                        />
                        <button
                          type="button"
                          className="password-toggle-btn"
                          onClick={() => setShowConfirmNewPassword((prev) => !prev)}
                          aria-label={showConfirmNewPassword ? 'Hide password' : 'Show password'}
                          tabIndex={0}
                        >
                          {showConfirmNewPassword ? (
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                              <line x1="1" y1="1" x2="23" y2="23" />
                            </svg>
                          ) : (
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                              <circle cx="12" cy="12" r="3" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>

                    <button type="submit" className="login-button confirm-reset-btn" disabled={isLoading}>
                      {isLoading ? 'Updating password...' : 'Update Password'}
                    </button>
                  </form>
                )}

                <div className="back-login-row">
                  <button
                    type="button"
                    className="back-login-btn"
                    onClick={() => {
                      setActiveTab('login');
                      setError(null);
                      setMessage(null);
                    }}
                    disabled={isLoading}
                  >
                    ← Back to Log In
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="login-footer">
            <p>Protected by Firebase Identity & Flask JWT authorization</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
