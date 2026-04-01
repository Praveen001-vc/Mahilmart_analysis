import os
import ipaddress
import socket
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def get_env_list(name, default=""):
    return [
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    ]


def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_debug_local_hosts():
    hosts = {"127.0.0.1", "localhost"}
    candidate_names = {socket.gethostname()}

    try:
        candidate_names.add(socket.getfqdn())
    except OSError:
        pass

    for name in candidate_names:
        if name:
            hosts.add(name)
        try:
            _, _, addresses = socket.gethostbyname_ex(name)
        except OSError:
            continue

        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue

            if parsed.version == 4 and (parsed.is_private or parsed.is_loopback):
                hosts.add(address)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
            probe_socket.connect(("8.8.8.8", 80))
            detected_address = probe_socket.getsockname()[0]
        parsed_detected_address = ipaddress.ip_address(detected_address)
        if (
            parsed_detected_address.version == 4
            and (
                parsed_detected_address.is_private
                or parsed_detected_address.is_loopback
            )
        ):
            hosts.add(detected_address)
    except (OSError, ValueError):
        pass

    return sorted(hosts)

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "mahilmart-dev-secret-key-change-this-in-production",
)
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = get_env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = get_env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if DEBUG:
    ALLOWED_HOSTS = sorted({*ALLOWED_HOSTS, *get_debug_local_hosts()})

    if not CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS = sorted(
            {f"http://{host}" for host in ALLOWED_HOSTS if host != "*"}
        )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "mahilmart_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tracker.context_processors.permission_context",
            ],
        },
    },
]

WSGI_APPLICATION = "mahilmart_project.wsgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "postgres").lower()

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "mahilmart_tracking"),
            "USER": os.getenv("POSTGRES_USER", "postgres"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = get_env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = get_env_bool("EMAIL_USE_SSL", False)
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    EMAIL_HOST_USER or "webmaster@localhost",
)
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
CONTACT_RECEIVER_EMAIL = os.getenv("CONTACT_RECEIVER_EMAIL", "")

# Keep users signed in unless they explicitly log out.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
