"""
Main Flask Application Hub
Role: Initialize Flask app, configure database and authentication, register blueprints, and expose health-check endpoint.

Key Features:
- SQLAlchemy ORM for database management
- JWT-Extended for secure token-based authentication
- CORS enabled for frontend integration
- Blueprint-based modular architecture
- Comprehensive error handling
"""

import sys

# Force stdout/stderr to use UTF-8 encoding to prevent UnicodeEncodeError on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
from datetime import timedelta
import os
from dotenv import load_dotenv
from services.ai.throttle import install_gemini_throttle
install_gemini_throttle()

# Load environment variables from absolute project root
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_BASE_DIR, '.env')
if os.path.exists(_ENV_PATH):
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
else:
    load_dotenv(override=True)

# Global SocketIO instance (defaults to 'threading' for Gunicorn gthread compatibility on Render)
socketio = SocketIO(async_mode=os.getenv('SOCKETIO_ASYNC_MODE', 'threading'))

# Import blueprints
from auth import auth_bp, register_jwt_error_handlers, signup, login, firebase_login, get_current_user, refresh, forgot_password, reset_password
from phase_two import phase_two_bp
from phase_four import phase_four_bp
from phase_five import phase_five_bp
from phase_live import phase_live_bp, init_socketio_events
from routes.presentation_rewriter import presentation_rewriter_bp
from routes.question_generator import question_generator_bp
from routes.presentation_generator import presentation_generator_bp
from services.download_service import MAX_UPLOAD_BYTES
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def _start_pptx_purge_worker(max_age_hours: int = 24, interval_seconds: int = 3600):
    """Background daemon thread to purge generated presentations and uploads older than max_age_hours."""
    import threading
    import time
    import glob

    # Protected extensions that must NEVER be deleted by purge worker
    PROTECTED_EXTENSIONS = {'.task', '.xml', '.onnx', '.model', '.bin', '.py', '.gitkeep', '.txt_meta'}
    PURGE_EXTENSIONS = {'.pptx', '.pdf', '.docx', '.txt', '.upload', '.png', '.jpg', '.jpeg', '.json'}

    def _purge_loop():
        while True:
            try:
                now = time.time()
                cutoff = now - (max_age_hours * 3600)
                folders = [
                    'downloads',
                    'generated_presentations',
                    os.path.join('instance', 'uploads'),
                    os.path.join('instance', 'generated_presentations'),
                ]
                for folder in folders:
                    if not os.path.exists(folder):
                        continue
                    for f in glob.glob(os.path.join(folder, '**', '*'), recursive=True):
                        if os.path.isfile(f):
                            ext = os.path.splitext(f)[1].lower()
                            if ext in PROTECTED_EXTENSIONS:
                                continue
                            if ext in PURGE_EXTENSIONS and os.path.getmtime(f) < cutoff:
                                try:
                                    os.remove(f)
                                    logger.info("[CLEANUP] Purged expired file: %s", f)
                                except OSError:
                                    pass
            except Exception as ex:
                logger.warning("[CLEANUP] File purge cycle warning: %s", ex)
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_purge_loop, daemon=True, name="pptx_purge_worker")
    thread.start()

def prewarm_ml_models():
    """
    Pre-warm ML models in background thread if explicitly enabled.
    Default is disabled (PREWARM_MODELS=false) to prevent Out-Of-Memory (OOM)
    kills and CPU starvation on Render Free Tier (512MB RAM limit).
    """
    enable_prewarm = os.getenv('PREWARM_MODELS', 'false').lower() in ('1', 'true', 'yes', 'on')
    if not enable_prewarm:
        logger.info("[PERF] Eager ML pre-warming skipped (PREWARM_MODELS=false). Models will load on demand.")
        return

    start = time.time()
    logger.info("[PERF] Pre-warming ML models in background...")

    try:
        from nlp_module.scoring_model import load_scoring_models
        load_scoring_models()
    except Exception as e:
        logger.warning(f"[PERF] Scoring model pre-warm notice: {e}")

    try:
        from services.viva_rag_engine import _load_sentence_model, _load_spacy
        _load_sentence_model()
        _load_spacy()
    except Exception as e:
        logger.warning(f"[PERF] SentenceTransformer/spaCy pre-warm notice: {e}")

    try:
        from services.coach_intent_engine import _get_intent_classifier
        _get_intent_classifier()
    except Exception as e:
        logger.warning(f"[PERF] Intent classifier pre-warm notice: {e}")

    elapsed = time.time() - start
    logger.info(f"[PERF] All ML models pre-warmed successfully in {elapsed:.3f}s")


def create_app():
    """
    Factory function to create and configure the Flask application.
    
    Initializes:
    - JWT authentication
    - CORS
    - Error handlers
    - Blueprints
    - MongoDB/Firestore connection check
    - Async pre-warmed ML Models
    """
    import threading
    
    # ===== CREATE FLASK APP =====
    app = Flask(__name__)

    # Pre-warm ML models in background thread if configured
    threading.Thread(target=prewarm_ml_models, daemon=True, name="ml_prewarm_worker").start()

    # Start TTL purge worker for generated files and uploads
    _start_pptx_purge_worker()

    # ===== JWT CONFIGURATION =====
    jwt_secret_key = os.getenv('JWT_SECRET_KEY', '').strip()
    if not jwt_secret_key:
        import secrets
        jwt_secret_key = os.getenv('SECRET_KEY', '').strip() or secrets.token_hex(32)
        logger.warning("[JWT] JWT_SECRET_KEY not explicitly configured. Using ephemeral session key.")
    
    app.config['JWT_SECRET_KEY'] = jwt_secret_key
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)
    
    # Initialize JWT with the app
    jwt = JWTManager(app)

    # ===== CORS CONFIGURATION =====
    cors_origins_env = os.getenv('CORS_ORIGINS', '').strip()
    if cors_origins_env and cors_origins_env != '*':
        parsed_origins = [o.strip() for o in cors_origins_env.split(',') if o.strip()]
        import re
        parsed_origins.append(re.compile(r"^https://.*\.vercel\.app$"))
        parsed_origins.append(re.compile(r"^https://.*\.web\.app$"))
        parsed_origins.append(re.compile(r"^https://.*\.firebaseapp\.com$"))
        parsed_origins.append(re.compile(r"^https://.*\.onrender\.com$"))
        parsed_origins.extend(['http://localhost:3000', 'http://localhost:5173', 'http://127.0.0.1:3000', 'http://127.0.0.1:5173'])
    else:
        parsed_origins = "*"

    # For SocketIO, cors_allowed_origins must be strings or '*'
    socketio_origins = '*'

    CORS(
        app,
        resources={r"/*": {
            "origins": parsed_origins,
            "allow_headers": ["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin"],
            "expose_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "supports_credentials": True,
        }},
    )

    # Flask enforces this before request handlers read multipart bodies. This
    # prevents oversized uploads from being copied to disk first.
    app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({
            'success': False,
            'message': f'File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.',
        }), 413

    # ===== SOCKET.IO CONFIGURATION =====
    socketio.init_app(app, cors_allowed_origins=socketio_origins)

    # ===== STARTUP HEALTH CHECK & VALIDATION =====
    with app.app_context():
        try:
            from models import verify_database_health
            db_health = verify_database_health()
            if db_health.get("status") == "ok":
                logger.info("[STARTUP OK] Database layer verified: %s", db_health.get("backend", "firestore"))
            else:
                logger.warning("[STARTUP WARN] Database running in degraded/in-memory mode: %s", db_health)
        except Exception as e:
            logger.warning("[STARTUP WARN] Database verification notice: %s. Using in-memory store.", e)

    # ===== REGISTER JWT ERROR HANDLERS =====
    # Handles expired, invalid, and missing JWT tokens on JWTManager (ISSUE-04)
    register_jwt_error_handlers(jwt, app)

    # ===== REGISTER BLUEPRINTS =====
    # Phase 1: Authentication
    app.register_blueprint(auth_bp)
    
    # Compatibility blueprint for /auth without /api prefix
    from flask import Blueprint as BP
    auth_compat_bp = BP('auth_compat', __name__, url_prefix='/auth')
    auth_compat_bp.add_url_rule('/firebase-login', 'firebase_login_compat', firebase_login, methods=['POST', 'OPTIONS'])
    auth_compat_bp.add_url_rule('/login', 'login_compat', login, methods=['POST', 'OPTIONS'])
    auth_compat_bp.add_url_rule('/signup', 'signup_compat', signup, methods=['POST', 'OPTIONS'])
    auth_compat_bp.add_url_rule('/me', 'me_compat', get_current_user, methods=['GET', 'OPTIONS'])
    auth_compat_bp.add_url_rule('/refresh', 'refresh_compat', refresh, methods=['POST', 'OPTIONS'])
    auth_compat_bp.add_url_rule('/forgot-password', 'forgot_password_compat', forgot_password, methods=['POST', 'OPTIONS'])
    auth_compat_bp.add_url_rule('/reset-password', 'reset_password_compat', reset_password, methods=['POST', 'OPTIONS'])
    app.register_blueprint(auth_compat_bp)
    
    # Phase 2: Document Analysis
    app.register_blueprint(phase_two_bp)
    
    # Phase 4: Speech Analysis
    app.register_blueprint(phase_four_bp)
    
    # Phase 5: AI Coach / Practice Mode
    app.register_blueprint(phase_five_bp)

    # Phase Live: Presentation Coach & Live Analyzer
    app.register_blueprint(phase_live_bp)
    init_socketio_events(socketio)

    # New Feature: AI Presentation Rewriter
    app.register_blueprint(presentation_rewriter_bp)

    # New Feature: Viva Question Generator
    app.register_blueprint(question_generator_bp)

    # New Feature: AI Presentation Generator
    app.register_blueprint(presentation_generator_bp)

    # ===== HEALTH-CHECK ENDPOINTS =====
    @app.route('/', methods=['GET'])
    def health_check():
        """Health check endpoint to verify the service is running."""
        from models import _is_firestore_enabled
        db_ready = _is_firestore_enabled()
        return jsonify({
            "status": "running",
            "service": "Presenova AI Presentation Platform",
            "version": "1.1.0",
            "database": "Firebase Firestore" if db_ready else "in-memory fallback"
        }), 200

    @app.route('/api/health', methods=['GET'])
    def api_health():
        """Render.com / uptime health check endpoint."""
        from models import _is_firestore_enabled
        db_ready = _is_firestore_enabled()
        return jsonify({
            "status": "ok",
            "environment": os.getenv('FLASK_ENV', 'development'),
            "database": "firestore" if db_ready else "in-memory",
            "service": "Presenova Backend",
            "version": "1.1.0"
        }), 200

    @app.route('/downloads/<path:filename>', methods=['GET'])
    def serve_download(filename):
        """Serve application installers (Windows, macOS, Android APK)."""
        candidate_dirs = [
            os.path.join(app.root_path, 'frontend', 'public', 'downloads'),
            os.path.join(app.root_path, 'downloads'),
        ]
        for d in candidate_dirs:
            if os.path.isfile(os.path.join(d, filename)):
                return send_from_directory(d, filename, as_attachment=True)
        return jsonify({"error": "File not found", "requested": filename}), 404

    return app


# Expose WSGI application instance for production servers (Gunicorn / Render)
app = create_app()

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5000'))

    # Local port collision probe: check if port is already held by an orphaned process
    import socket
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_sock.bind((host, port))
        test_sock.close()
    except OSError as bind_err:
        logger.critical(
            f"[FATAL ERROR] Port {port} is already in use by another process! "
            f"Please terminate any orphaned process holding port {port} before restarting."
        )
        sys.exit(1)

    # Run the Flask development server wrapped with Socket.IO
    # use_reloader is explicitly False to prevent orphaned background reloader processes
    debug = os.getenv('FLASK_DEBUG', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    socketio_kwargs = {
        'host': host,
        'port': port,
        'debug': debug,
        'use_reloader': False,
        'allow_unsafe_werkzeug': True,
    }

    logger.info(f"Starting Presenova server on http://{host}:{port} (debug={debug}, reloader=False)")
    socketio.run(app, **socketio_kwargs)


