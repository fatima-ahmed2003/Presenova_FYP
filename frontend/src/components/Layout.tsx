/**
 * Layout Component
 * Vertical sidebar navigation + main content area
 */

import React, { useState } from 'react';
import { Outlet, Link, NavLink, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './Layout.css';

/* ---------- Tiny inline icon set (no extra package needed) ---------- */
const ICONS: Record<string, React.ReactNode> = {
  dashboard: (
    <>
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </>
  ),
  document: (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </>
  ),
  mic: (
    <>
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </>
  ),
  live: (
    <>
      <polygon points="23 7 16 12 23 17 23 7" />
      <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
    </>
  ),
  chat: (
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  ),
  rewrite: (
    <>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </>
  ),
  generate: <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />,
  phone: (
    <>
      <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
      <line x1="12" y1="18" x2="12.01" y2="18" />
    </>
  ),
  logout: (
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </>
  ),
  menu: (
    <>
      <line x1="3" y1="6" x2="21" y2="6" />
      <line x1="3" y1="12" x2="21" y2="12" />
      <line x1="3" y1="18" x2="21" y2="18" />
    </>
  ),
};

const Icon: React.FC<{ name: string; size?: number }> = ({ name, size = 20 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {ICONS[name]}
  </svg>
);

/* ---------- Nav items (same routes as before) ---------- */
const NAV_ITEMS = [
  { to: '/analytics', label: 'Dashboard', icon: 'dashboard' },
  { to: '/analyzer', label: 'Document Analyzer', icon: 'document' },
  { to: '/speech', label: 'Speech Analyzer', icon: 'mic' },
  { to: '/live-coach', label: 'Live Coach', icon: 'live' },
  { to: '/practice', label: 'AI Coach', icon: 'chat' },
  { to: '/presentation-rewriter', label: 'Rewriter', icon: 'rewrite' },
  { to: '/presentation-generator', label: 'Generator', icon: 'generate' },
  { to: '/download', label: 'App', icon: 'phone' },
];

const Layout: React.FC = () => {
  const { isAuthenticated, isLoading, user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  // Close mobile sidebar when route changes
  React.useEffect(() => {
    setSidebarOpen(false);
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

  const closeSidebar = () => setSidebarOpen(false);

  const displayName = user?.name || 'User';
  const initials =
    displayName
      .split(' ')
      .filter(Boolean)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase() || 'U';

  return (
    <div className="layout">
      {/* ===== Vertical Sidebar ===== */}
      <aside className={`app-sidebar ${sidebarOpen ? 'open' : ''}`}>
        <Link to="/analytics" className="sidebar-brand" onClick={closeSidebar}>
          <img src="/logo-icon.png" alt="Presenova logo" className="sidebar-brand-img" />
          <span>Presenova</span>
        </Link>

        <nav className="sidebar-nav" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
              onClick={closeSidebar}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <Link to="/analytics" className="sidebar-user" title={user?.email} onClick={closeSidebar}>
            <div className="sidebar-avatar">{initials}</div>
            <div className="sidebar-user-text">
              <span className="sidebar-user-name">{displayName}</span>
              {user?.email && <span className="sidebar-user-email">{user.email}</span>}
            </div>
          </Link>

          <button onClick={logout} className="sidebar-logout" title="Logout">
            <Icon name="logout" />
            <span>Logout</span>
          </button>
        </div>
      </aside>

      {/* Mobile backdrop */}
      {sidebarOpen && <div className="sidebar-backdrop" onClick={closeSidebar} />}

      {/* ===== Right side: topbar (mobile) + content + footer ===== */}
      <div className="layout-body">
        <header className="mobile-topbar">
          <button
            className="mobile-menu-btn"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open navigation menu"
            aria-expanded={sidebarOpen}
          >
            <Icon name="menu" />
          </button>
          <Link to="/analytics" className="mobile-topbar-logo">
            <img src="/logo-icon.png" alt="Presenova logo" />
            <span>Presenova</span>
          </Link>
        </header>

        <main className="main-content">
          <Outlet />
        </main>

        <footer className="footer">
          <p>&copy; 2026 Presenova AI Presentation Platform | FYP Project</p>
        </footer>
      </div>
    </div>
  );
};

export default Layout;