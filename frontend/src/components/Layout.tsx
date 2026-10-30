/**
 * Layout Component
 * Global wrapper containing Navigation Bar and main content area
 */

import React, { useState } from 'react';
import { Outlet, Link, NavLink, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './Layout.css';

const Layout: React.FC = () => {
  const { isAuthenticated, isLoading, user, logout } = useAuth();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const location = useLocation();

  // Close mobile drawer when route changes
  React.useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  if (isLoading) {
    return (
      <div className="app-loading">
        <div className="spinner"></div>
        <p>Loading user session...</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  const toggleMobileMenu = () => {
    setMobileMenuOpen((prev) => !prev);
  };

  const closeMobileMenu = () => {
    setMobileMenuOpen(false);
  };

  return (
    <div className="layout">
      {/* Navigation Bar */}
      <nav className="navbar">
        <div className="navbar-container">
          <Link to="/analytics" className="navbar-logo" onClick={closeMobileMenu}>
            <img src="/logo-icon.png" alt="Presenova logo" className="navbar-logo-img" />
            <span>Presenova</span>
          </Link>

          {/* Desktop Nav Links */}
          <ul className="nav-links desktop-only">
            <li className="nav-item">
              <NavLink to="/analyzer" className="nav-link">
                Document Analyzer
              </NavLink>
            </li>

            <li className="nav-item">
              <NavLink to="/speech" className="nav-link">
                Speech Analyzer
              </NavLink>
            </li>

            <li className="nav-item">
              <NavLink to="/live-coach" className="nav-link">
                Live Coach
              </NavLink>
            </li>

            <li className="nav-item">
              <NavLink to="/practice" className="nav-link">
                AI Coach
              </NavLink>
            </li>

            <li className="nav-item">
              <NavLink to="/presentation-rewriter" className="nav-link">
                Rewriter
              </NavLink>
            </li>

            <li className="nav-item">
              <NavLink to="/presentation-generator" className="nav-link">
                Generator
              </NavLink>
            </li>

            <li className="nav-item">
              <NavLink to="/download" className="nav-link">
                App
              </NavLink>
            </li>
          </ul>

          <div className="navbar-actions">
            <Link to="/analytics" className="user-name" title={user?.email}>
              {user?.name || 'User'}
            </Link>
            <button onClick={logout} className="logout-btn desktop-only" title="Logout">
              Logout
            </button>

            {/* Mobile Hamburger Button */}
            <button
              className={`hamburger-btn ${mobileMenuOpen ? 'active' : ''}`}
              onClick={toggleMobileMenu}
              aria-label="Toggle navigation menu"
              aria-expanded={mobileMenuOpen}
            >
              <span className="hamburger-line"></span>
              <span className="hamburger-line"></span>
              <span className="hamburger-line"></span>
            </button>
          </div>
        </div>

        {/* Mobile Slide-down Drawer */}
        {mobileMenuOpen && (
          <div className="mobile-drawer-overlay" onClick={closeMobileMenu}>
            <div className="mobile-drawer-content" onClick={(e) => e.stopPropagation()}>
              <div className="mobile-drawer-header">
                <span className="mobile-user-info">Logged in as <strong>{user?.name}</strong></span>
                <button className="mobile-close-btn" onClick={closeMobileMenu} aria-label="Close menu">✕</button>
              </div>

              <ul className="mobile-nav-links">
                <li>
                  <NavLink to="/analytics" className="mobile-nav-link" onClick={closeMobileMenu}>
                    📊 Dashboard & Analytics
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/analyzer" className="mobile-nav-link" onClick={closeMobileMenu}>
                    📄 Document Analyzer
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/speech" className="mobile-nav-link" onClick={closeMobileMenu}>
                    🎙️ Speech Analyzer
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/live-coach" className="mobile-nav-link" onClick={closeMobileMenu}>
                    🎯 Live Presentation Coach
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/practice" className="mobile-nav-link" onClick={closeMobileMenu}>
                    💬 AI Practice Coach
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/presentation-rewriter" className="mobile-nav-link" onClick={closeMobileMenu}>
                    ✨ AI Presentation Rewriter
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/presentation-generator" className="mobile-nav-link" onClick={closeMobileMenu}>
                    🚀 Presentation Generator
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/download" className="mobile-nav-link" onClick={closeMobileMenu}>
                    📲 Download Apps (APK/Desktop)
                  </NavLink>
                </li>
              </ul>

              <div className="mobile-drawer-footer">
                <button onClick={() => { closeMobileMenu(); logout(); }} className="mobile-logout-btn">
                  Logout
                </button>
              </div>
            </div>
          </div>
        )}
      </nav>

      {/* Main Content */}
      <main className="main-content">
        <Outlet />
      </main>

      {/* Footer */}
      <footer className="footer">
        <p>&copy; 2026 Presenova AI Presentation Platform | FYP Project</p>
      </footer>
    </div>
  );
};

export default Layout;
