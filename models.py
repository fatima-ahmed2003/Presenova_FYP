"""
Firebase Firestore Database Models (with robust In-Memory Fallback)
Role: Define helper classes for User, Upload, Report, PresentationSession, and HistoricalReport entities.
Uses Firestore as primary database, with an in-memory store fallback if Firestore API is offline or disabled.
"""

import os
import json
from datetime import datetime, timezone
import uuid
import logging
import threading

from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_BASE_DIR, '.env')
if os.path.exists(_ENV_PATH):
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
else:
    load_dotenv(override=True)

logger = logging.getLogger(__name__)

# ── Thread Lock for Storage & Local Store Persistence ─────────────────────────
_store_lock = threading.RLock()

# ── Local Persistent Disk Fallback ───────────────────────────────────────────
_INSTANCE_DIR = os.path.join(_BASE_DIR, 'instance')
os.makedirs(_INSTANCE_DIR, exist_ok=True)
_LOCAL_STORE_PATH = os.path.join(_INSTANCE_DIR, 'presenova_store.json')

# ── In-Memory / Local Storage Fallback ───────────────────────────────────────
_MEMORY_STORE = {
    "users": {},                 # id -> dict
    "uploads": {},               # id -> dict
    "reports": {},               # id -> dict
    "presentation_sessions": {}, # id -> dict
    "historical_reports": {},    # id -> dict
}

def _json_serial(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def _load_local_store():
    global _MEMORY_STORE
    with _store_lock:
        if os.path.exists(_LOCAL_STORE_PATH):
            try:
                with open(_LOCAL_STORE_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k in ("users", "uploads", "reports", "presentation_sessions", "historical_reports"):
                            if k in data and isinstance(data[k], dict):
                                _MEMORY_STORE[k].update(data[k])
                logger.info(f"[DB LOCAL] Loaded persistent store from {_LOCAL_STORE_PATH}: {len(_MEMORY_STORE['users'])} users, {len(_MEMORY_STORE['reports'])} reports")
            except Exception as e:
                logger.warning(f"[DB LOCAL] Could not load persistent store: {e}")

def _save_local_store():
    with _store_lock:
        try:
            tmp_path = _LOCAL_STORE_PATH + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(_MEMORY_STORE, f, default=_json_serial, indent=2)
            os.replace(tmp_path, _LOCAL_STORE_PATH)
        except Exception as e:
            logger.warning(f"[DB LOCAL] Could not save persistent store: {e}")

_use_firestore = True

# ── Firebase Admin SDK setup ─────────────────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, firestore as fs

_firebase_app = None
db = None

FIRESTORE_TIMEOUT = 10.0

def _disable_firestore():
    global _use_firestore
    with _store_lock:
        _use_firestore = False

def _is_firestore_enabled():
    with _store_lock:
        return _use_firestore and db is not None

def _sanitize_credential_dict(cred_dict: dict) -> dict:
    """Ensure private_key has proper newlines and not literal escaped \\n strings."""
    if isinstance(cred_dict, dict) and "private_key" in cred_dict:
        pk = cred_dict.get("private_key", "")
        if isinstance(pk, str) and "\\n" in pk:
            cred_dict["private_key"] = pk.replace("\\n", "\n")
    return cred_dict

def _init_firebase():
    global _firebase_app, db
    if _firebase_app is not None:
        return

    # Always ensure local persistent data is loaded
    _load_local_store()

    cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-service-account.json')
    if not os.path.isabs(cred_path):
        cred_path = os.path.join(_BASE_DIR, cred_path)

    cred_json_str = os.getenv('FIREBASE_CREDENTIALS_JSON', '').strip()
    private_key_env = os.getenv('FIREBASE_PRIVATE_KEY', '').strip()
    client_email_env = os.getenv('FIREBASE_CLIENT_EMAIL', '').strip()
    project_id_env = os.getenv('FIREBASE_PROJECT_ID', '').strip()

    _flask_env = os.getenv('FLASK_ENV', 'development').strip().lower()
    is_production = _flask_env == 'production'

    if is_production and os.path.exists(cred_path) and not cred_json_str:
        logger.critical(
            "[SECURITY WARNING] firebase-service-account.json is present on disk in PRODUCTION. "
            "This file contains a private key and must NEVER be deployed to production. "
            "Set FIREBASE_CREDENTIALS_JSON in Render's secret store instead, and delete this file."
        )

    try:
        cred = None
        if cred_json_str:
            cred_dict = json.loads(cred_json_str)
            cred_dict = _sanitize_credential_dict(cred_dict)
            cred = credentials.Certificate(cred_dict)
            logger.info("[DB OK] Firebase initialized from FIREBASE_CREDENTIALS_JSON env var")
        elif private_key_env and client_email_env:
            cred_dict = {
                "type": "service_account",
                "project_id": project_id_env or "presenova-fyp",
                "private_key": private_key_env.replace("\\n", "\n"),
                "client_email": client_email_env,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            cred = credentials.Certificate(cred_dict)
            logger.info("[DB OK] Firebase initialized from FIREBASE_PRIVATE_KEY & FIREBASE_CLIENT_EMAIL env vars")
        elif os.path.exists(cred_path):
            with open(cred_path, 'r', encoding='utf-8') as f:
                raw_dict = json.load(f)
            cred_dict = _sanitize_credential_dict(raw_dict)
            cred = credentials.Certificate(cred_dict)
            logger.info("[DB OK] Firebase initialized from credentials file: %s", cred_path)
        else:
            allow_in_memory = os.getenv('ALLOW_IN_MEMORY_DB', 'true').lower() in ('1', 'true', 'yes', 'on')
            if is_production and not allow_in_memory:
                logger.error("[DB ERROR] Firebase credentials not found in production. Activating in-memory database fallback.")
            else:
                logger.warning("[DB WARN] Firebase credentials not found. Using in-memory database.")
            _disable_firestore()
            return

        _firebase_app = firebase_admin.initialize_app(cred)

        # Fast credential probe: verifies token refresh in < 2 seconds to detect bad credentials or clock skew
        try:
            import google.auth.transport.requests
            req = google.auth.transport.requests.Request()
            cred.get_credential().refresh(req)
            db = fs.client()
            logger.info("[DB OK] Connected to Firebase Firestore and credentials verified.")
        except Exception as auth_err:
            logger.error("[DB ERROR] Firebase credentials verification failed: %s", auth_err)
            logger.warning("[DB WARN] Falling back to persistent database fallback.")
            _disable_firestore()
            return

    except Exception as e:
        logger.error("[DB ERROR] Firebase initialization error: %s", e)
        logger.warning("[DB WARN] Fallback to persistent database active.")
        _disable_firestore()

def verify_database_health() -> dict:
    """
    Startup health probe: verifies that the database layer is functional.
    """
    if _is_firestore_enabled():
        try:
            _ = db.collection("users").limit(1).get(timeout=3.0)
            return {"status": "ok", "backend": "firestore", "connected": True}
        except Exception as exc:
            msg = f"Firestore health check failed: {exc}"
            logger.warning(f"[HEALTH WARN] {msg}. Using in-memory fallback.")
            return {"status": "degraded", "backend": "in-memory-fallback", "error": str(exc)}
    else:
        return {"status": "ok", "backend": "in-memory", "connected": False}

_init_firebase()


# ── Helper utilities ──────────────────────────────────────────────────────────

def _to_dict(doc_snapshot) -> dict | None:
    if doc_snapshot and doc_snapshot.exists:
        return doc_snapshot.to_dict()
    return None


def _serialize_dt(dt) -> str | None:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else None


# ─────────────────────────────────────────────────────────────────────────────
# User Model
# ─────────────────────────────────────────────────────────────────────────────

class User:
    """User class — maps to Firestore 'users' collection or in-memory fallback."""

    def __init__(self, id, uid=None, name="", email="", photo_url=None, provider="password", password_hash=None, created_at=None, updated_at=None):
        self.id = id
        self.uid = uid or id
        self.name = name
        self.email = email
        self.photo_url = photo_url
        self.provider = provider or "password"
        self.password_hash = password_hash
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)

    def save(self) -> None:
        """Persist current user state to Firestore and in-memory store."""
        self.updated_at = datetime.now(timezone.utc)
        doc = {
            "name": self.name,
            "photo_url": self.photo_url,
            "provider": self.provider,
            "updated_at": self.updated_at,
        }
        if self.password_hash:
            doc["password_hash"] = self.password_hash

        with _store_lock:
            if self.id in _MEMORY_STORE["users"]:
                _MEMORY_STORE["users"][self.id].update(doc)
            elif self.uid in _MEMORY_STORE["users"]:
                _MEMORY_STORE["users"][self.uid].update(doc)
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("users").document(str(self.id)).set(doc, merge=True, timeout=FIRESTORE_TIMEOUT)
            except Exception as e:
                logger.warning(f"[DB FALLBACK] Firestore error on User.save: {e}")

    def update_password(self, new_password_hash: str) -> None:
        """Safely update user's hashed password in both Firestore and in-memory store."""
        self.password_hash = new_password_hash
        self.updated_at = datetime.now(timezone.utc)
        doc = {
            "password_hash": self.password_hash,
            "updated_at": self.updated_at,
        }
        with _store_lock:
            if self.id in _MEMORY_STORE["users"]:
                _MEMORY_STORE["users"][self.id].update(doc)
            elif self.uid in _MEMORY_STORE["users"]:
                _MEMORY_STORE["users"][self.uid].update(doc)
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("users").document(str(self.id)).set(doc, merge=True, timeout=FIRESTORE_TIMEOUT)
            except Exception as e:
                logger.warning(f"[DB FALLBACK] Firestore error on User.update_password: {e}")

    @staticmethod
    def get_by_email(email: str) -> "User | None":
        email_clean = (email or "").lower().strip()
        if _is_firestore_enabled():
            try:
                results = list(db.collection("users").where("email", "==", email_clean).limit(1).stream(timeout=FIRESTORE_TIMEOUT))
                for doc in results:
                    d = doc.to_dict()
                    return User(
                        id=d.get("id"), uid=d.get("uid") or d.get("id"),
                        name=d.get("name"), email=d.get("email"),
                        photo_url=d.get("photo_url"), provider=d.get("provider", "password"),
                        password_hash=d.get("password_hash"),
                        created_at=d.get("created_at"), updated_at=d.get("updated_at")
                    )
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on get_by_email: {e}. Falling back to in-memory store.")

        # In-memory / persistent disk search with thread lock
        with _store_lock:
            users_list = list(_MEMORY_STORE["users"].values())

        for u in users_list:
            if (u.get("email") or "").lower().strip() == email_clean:
                return User(
                    id=u.get("id"), uid=u.get("uid") or u.get("id"),
                    name=u.get("name"), email=u.get("email"),
                    photo_url=u.get("photo_url"), provider=u.get("provider", "password"),
                    password_hash=u.get("password_hash"),
                    created_at=u.get("created_at"), updated_at=u.get("updated_at")
                )
        return None

    @staticmethod
    def get_by_id(user_id: str) -> "User | None":
        if _is_firestore_enabled():
            try:
                doc = db.collection("users").document(user_id).get(timeout=FIRESTORE_TIMEOUT)
                d = _to_dict(doc)
                if d:
                    return User(
                        id=d.get("id"), uid=d.get("uid") or d.get("id"),
                        name=d.get("name"), email=d.get("email"),
                        photo_url=d.get("photo_url"), provider=d.get("provider", "password"),
                        password_hash=d.get("password_hash"),
                        created_at=d.get("created_at"), updated_at=d.get("updated_at")
                    )
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on get_by_id: {e}. Falling back to in-memory store.")

        # In-memory lookup with thread lock
        with _store_lock:
            u = _MEMORY_STORE["users"].get(user_id)

        if u:
            return User(
                id=u.get("id"), uid=u.get("uid") or u.get("id"),
                name=u.get("name"), email=u.get("email"),
                photo_url=u.get("photo_url"), provider=u.get("provider", "password"),
                password_hash=u.get("password_hash"),
                created_at=u.get("created_at"), updated_at=u.get("updated_at")
            )
        return None

    @staticmethod
    def create(name: str, email: str, photo_url: str = None, provider: str = "password", password_hash: str = None) -> "User":
        user_id = str(uuid.uuid4())
        return User.create_with_id(user_id, name, email, photo_url, provider, password_hash)

    @staticmethod
    def create_with_id(user_id: str, name: str, email: str, photo_url: str = None, provider: str = "password", password_hash: str = None) -> "User":
        now = datetime.now(timezone.utc)
        doc = {
            "id": user_id,
            "uid": user_id,
            "name": name.strip() if name else "",
            "email": email.lower().strip() if email else "",
            "photo_url": photo_url,
            "provider": provider or "password",
            "password_hash": password_hash,
            "created_at": now,
            "updated_at": now,
        }
        with _store_lock:
            _MEMORY_STORE["users"][user_id] = doc
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("users").document(user_id).set(doc, merge=True, timeout=FIRESTORE_TIMEOUT)
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on User.create_with_id: {e}")

        return User(
            id=user_id, uid=user_id, name=name, email=email,
            photo_url=photo_url, provider=provider, password_hash=password_hash,
            created_at=now, updated_at=now
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "email": self.email,
            "photo_url": self.photo_url,
            "provider": self.provider,
            "created_at": _serialize_dt(self.created_at),
            "updated_at": _serialize_dt(self.updated_at),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Upload Model
# ─────────────────────────────────────────────────────────────────────────────

class Upload:
    """Upload class — maps to Firestore 'uploads' collection or in-memory fallback."""

    def __init__(self, id, filename, mime_type, file_path, user_id, created_at=None):
        self.id = id
        self.filename = filename
        self.mime_type = mime_type
        self.file_path = file_path
        self.user_id = user_id
        self.created_at = created_at or datetime.now(timezone.utc)

    @staticmethod
    def create(filename: str, mime_type: str, file_path: str, user_id: str) -> "Upload":
        upload_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        doc = {
            "id": upload_id,
            "filename": filename,
            "mime_type": mime_type,
            "file_path": file_path,
            "user_id": user_id,
            "created_at": now,
        }
        with _store_lock:
            _MEMORY_STORE["uploads"][upload_id] = doc
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("uploads").document(upload_id).set(doc)
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on Upload.create: {e}")

        return Upload(
            id=upload_id, filename=filename, mime_type=mime_type,
            file_path=file_path, user_id=user_id, created_at=now
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "user_id": self.user_id,
            "created_at": _serialize_dt(self.created_at),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Report Model
# ─────────────────────────────────────────────────────────────────────────────

class Report:
    """Report class — maps to Firestore 'reports' collection or in-memory fallback."""

    def __init__(self, id, report_json, report_type, user_id, upload_id=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.report_json = report_json
        self.report_type = report_type
        self.user_id = user_id
        self.upload_id = upload_id
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)

    @staticmethod
    def create(report_json: dict, report_type: str, user_id: str,
               upload_id: str | None = None) -> "Report":
        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        doc = {
            "id": report_id,
            "report_json": report_json,
            "report_type": report_type,
            "user_id": user_id,
            "upload_id": upload_id,
            "created_at": now,
            "updated_at": now,
        }
        with _store_lock:
            _MEMORY_STORE["reports"][report_id] = doc
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("reports").document(report_id).set(doc)
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on Report.create: {e}")

        return Report(
            id=report_id, report_json=report_json, report_type=report_type,
            user_id=user_id, upload_id=upload_id, created_at=now, updated_at=now
        )

    @staticmethod
    def get_by_user(user_id: str) -> list["Report"]:
        if _is_firestore_enabled():
            try:
                results = list(
                    db.collection("reports")
                    .where("user_id", "==", user_id)
                    .stream()
                )
                reports = []
                for doc in results:
                    d = doc.to_dict()
                    reports.append(Report(
                        id=d.get("id"), report_json=d.get("report_json"),
                        report_type=d.get("report_type"), user_id=d.get("user_id"),
                        upload_id=d.get("upload_id"), created_at=d.get("created_at"),
                        updated_at=d.get("updated_at")
                    ))
                return reports
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on Report.get_by_user: {e}")

        # In-memory search with thread lock
        with _store_lock:
            reports_list = list(_MEMORY_STORE["reports"].values())

        reports = []
        for r in reports_list:
            if r.get("user_id") == user_id:
                reports.append(Report(
                    id=r.get("id"), report_json=r.get("report_json"),
                    report_type=r.get("report_type"), user_id=r.get("user_id"),
                    upload_id=r.get("upload_id"), created_at=r.get("created_at"),
                    updated_at=r.get("updated_at")
                ))
        return sorted(reports, key=lambda x: str(x.created_at), reverse=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "report_type": self.report_type,
            "report_json": self.report_json,
            "user_id": self.user_id,
            "upload_id": self.upload_id,
            "created_at": _serialize_dt(self.created_at),
        }


# ─────────────────────────────────────────────────────────────────────────────
# PresentationSession Model
# ─────────────────────────────────────────────────────────────────────────────

class PresentationSession:
    """PresentationSession — maps to Firestore 'presentation_sessions' collection."""

    def __init__(self, id, user_id, topic, status, started_at=None, ended_at=None, metrics=None):
        self.id = id
        self.user_id = user_id
        self.topic = topic
        self.status = status
        self.started_at = started_at or datetime.now(timezone.utc)
        self.ended_at = ended_at or datetime.now(timezone.utc)
        self.metrics = metrics or {
            "eye_contact_scores": [],
            "posture_scores": [],
            "head_movement_scores": [],
            "video_frames_received": 0,
            "video_frames_analyzed": 0,
            "video_frames_dropped": 0,
            "video_frames_with_face": 0,
            "video_frames_with_gaze": 0,
            "video_frames_with_posture": 0,
            "video_frames_with_head_movement": 0,
            "wpm_history": [],
            "fillers_detected": 0,
            "transcripts": [],
            "interruptions": [],
            "confidence_scores": [],
            "vocal_sentiment_scores": [],
        }

    @staticmethod
    def create(user_id: str, topic: str) -> "PresentationSession":
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        default_metrics = {
            "eye_contact_scores": [],
            "posture_scores": [],
            "head_movement_scores": [],
            "video_frames_received": 0,
            "video_frames_analyzed": 0,
            "video_frames_dropped": 0,
            "video_frames_with_face": 0,
            "video_frames_with_gaze": 0,
            "video_frames_with_posture": 0,
            "video_frames_with_head_movement": 0,
            "wpm_history": [],
            "fillers_detected": 0,
            "transcripts": [],
            "interruptions": [],
            "confidence_scores": [],
            "vocal_sentiment_scores": [],
        }
        doc = {
            "id": session_id,
            "user_id": user_id,
            "topic": topic,
            "status": "STREAMING",
            "started_at": now,
            "ended_at": now,
            "metrics": default_metrics,
        }
        with _store_lock:
            _MEMORY_STORE["presentation_sessions"][session_id] = doc
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("presentation_sessions").document(session_id).set(doc)
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on PresentationSession.create: {e}")

        return PresentationSession(
            id=session_id, user_id=user_id, topic=topic,
            status="STREAMING", started_at=now, ended_at=now, metrics=default_metrics
        )

    @staticmethod
    def get_by_id(session_id: str) -> "PresentationSession | None":
        # Fast path: check active in-memory sessions first to prevent socket blocking
        with _store_lock:
            s = _MEMORY_STORE["presentation_sessions"].get(session_id)

        if s:
            return PresentationSession(
                id=s.get("id"), user_id=s.get("user_id"),
                topic=s.get("topic"), status=s.get("status"),
                started_at=s.get("started_at"), ended_at=s.get("ended_at"),
                metrics=s.get("metrics")
            )

        if _is_firestore_enabled():
            try:
                doc = db.collection("presentation_sessions").document(session_id).get()
                d = _to_dict(doc)
                if d:
                    sess = PresentationSession(
                        id=d.get("id"), user_id=d.get("user_id"),
                        topic=d.get("topic"), status=d.get("status"),
                        started_at=d.get("started_at"), ended_at=d.get("ended_at"),
                        metrics=d.get("metrics")
                    )
                    with _store_lock:
                        _MEMORY_STORE["presentation_sessions"][session_id] = d
                    return sess
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on PresentationSession.get_by_id: {e}")

        return None

    def update_metrics(self, key: str, value) -> None:
        # ISSUE-20 / AUDIT-07: Perform list append and memory store serialization atomically under _store_lock.
        # The authoritative in-memory state is always updated synchronously here.
        with _store_lock:
            if self.metrics and key in self.metrics:
                if isinstance(self.metrics[key], list):
                    self.metrics[key].append(value)
            if self.id in _MEMORY_STORE["presentation_sessions"]:
                _MEMORY_STORE["presentation_sessions"][self.id]["metrics"] = self.metrics
                _save_local_store()

        # AUDIT-07: Firestore write is dispatched on a background daemon thread to avoid
        # blocking the SocketIO event loop during high-frequency real-time streaming
        # (up to 10 frames/sec per user). Data is already safe in _MEMORY_STORE above.
        if _is_firestore_enabled():
            session_id = self.id

            def _fs_update():
                try:
                    ref = db.collection("presentation_sessions").document(session_id)
                    ref.update({f"metrics.{key}": fs.ArrayUnion([value])})
                except Exception as e:
                    logger.debug(f"[DB] Firestore async metric update notice: {e}")

            threading.Thread(target=_fs_update, daemon=True).start()

    def increment_metric(self, key: str, val: int = 1) -> None:
        with _store_lock:
            if self.metrics and key in self.metrics:
                self.metrics[key] = self.metrics.get(key, 0) + val
            if self.id in _MEMORY_STORE["presentation_sessions"]:
                _MEMORY_STORE["presentation_sessions"][self.id]["metrics"] = self.metrics
                _save_local_store()

        # AUDIT-07: Firestore increment also runs on a background thread to prevent socket stall
        if _is_firestore_enabled():
            session_id = self.id

            def _fs_increment():
                try:
                    ref = db.collection("presentation_sessions").document(session_id)
                    ref.update({f"metrics.{key}": fs.Increment(val)})
                except Exception as e:
                    logger.warning(f"[DB FALLBACK] Firestore async increment_metric error: {e}")

            threading.Thread(target=_fs_increment, daemon=True).start()

    def update_status(self, new_status: str) -> None:
        self.status = new_status
        now = datetime.now(timezone.utc)
        with _store_lock:
            if self.id in _MEMORY_STORE["presentation_sessions"]:
                _MEMORY_STORE["presentation_sessions"][self.id]["status"] = new_status
                _MEMORY_STORE["presentation_sessions"][self.id]["ended_at"] = now
                _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("presentation_sessions").document(self.id).update({
                    "status": new_status,
                    "ended_at": now,
                })
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore update_status error: {e}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "topic": self.topic,
            "status": self.status,
            "started_at": _serialize_dt(self.started_at),
            "ended_at": _serialize_dt(self.ended_at),
            "metrics": self.metrics,
        }


# ─────────────────────────────────────────────────────────────────────────────
# HistoricalReport Model
# ─────────────────────────────────────────────────────────────────────────────

class HistoricalReport:
    """HistoricalReport — maps to Firestore 'historical_reports' collection."""

    def __init__(self, id, session_id, user_id, topic, report_json, created_at=None):
        self.id = id
        self.session_id = session_id
        self.user_id = user_id
        self.topic = topic
        self.report_json = report_json
        self.created_at = created_at or datetime.now(timezone.utc)

    @staticmethod
    def create(session_id: str, user_id: str, topic: str, report_json: dict) -> "HistoricalReport":
        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        doc = {
            "id": report_id,
            "session_id": session_id,
            "user_id": user_id,
            "topic": topic.strip().lower(),
            "report_json": report_json,
            "created_at": now,
        }
        with _store_lock:
            _MEMORY_STORE["historical_reports"][report_id] = doc
            _save_local_store()

        if _is_firestore_enabled():
            try:
                db.collection("historical_reports").document(report_id).set(doc)
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on HistoricalReport.create: {e}")

        return HistoricalReport(
            id=report_id, session_id=session_id, user_id=user_id,
            topic=topic, report_json=report_json, created_at=now
        )

    @staticmethod
    def get_by_user_and_topic(user_id: str, topic: str) -> list["HistoricalReport"]:
        topic_lower = topic.strip().lower()
        if _is_firestore_enabled():
            try:
                results = list(
                    db.collection("historical_reports")
                    .where("user_id", "==", user_id)
                    .where("topic", "==", topic_lower)
                    .stream()
                )
                reports = []
                for doc in results:
                    d = doc.to_dict()
                    reports.append(HistoricalReport(
                        id=d.get("id"), session_id=d.get("session_id"),
                        user_id=d.get("user_id"), topic=d.get("topic"),
                        report_json=d.get("report_json"), created_at=d.get("created_at")
                    ))
                return reports
            except Exception as e:
                logger.warning(f"[DB WARN] Firestore error on get_by_user_and_topic: {e}")

        with _store_lock:
            hr_list = list(_MEMORY_STORE["historical_reports"].values())

        reports = []
        for hr in hr_list:
            if hr.get("user_id") == user_id and hr.get("topic") == topic_lower:
                reports.append(HistoricalReport(
                    id=hr.get("id"), session_id=hr.get("session_id"),
                    user_id=hr.get("user_id"), topic=hr.get("topic"),
                    report_json=hr.get("report_json"), created_at=hr.get("created_at")
                ))
        return sorted(reports, key=lambda x: str(x.created_at), reverse=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "topic": self.topic,
            "report_json": self.report_json,
            "created_at": _serialize_dt(self.created_at),
        }
