from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("SECRET_KEY", default="inseguro-solo-desarrollo")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "simple_history",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "apps.nucleo",
    "apps.empresas",
    "apps.usuarios",
    "apps.auditoria",
    "apps.productos",
    "apps.depositos",
    "apps.proveedores",
    "apps.compras",
    "apps.lotes",
    "apps.inventario",
    "apps.clientes",
    "apps.mercaderia",
    "apps.series",
    "apps.kits",
    "apps.envases",
    "apps.consignacion",
    "apps.elaboracion",
    "apps.trazabilidad",
    "apps.cadena_frio",
    "apps.calidad",
    "apps.consumo",
    "apps.analisis",
    "apps.produccion",
    "apps.desposte",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.nucleo.middleware.EmpresaActivaMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "apps.nucleo.context_processors.contexto_empresa",
            ],
            "builtins": ["apps.nucleo.templatetags.formato"],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default="postgres://impulso:impulso@localhost:55433/impulso")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "usuarios.Usuario"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "usuarios:login"
LOGIN_REDIRECT_URL = "usuarios:seleccionar_empresa"
LOGOUT_REDIRECT_URL = "usuarios:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "apps.nucleo.api_permisos.TieneEmpresaActiva",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "COERCE_DECIMAL_TO_STRING": True,
    "EXCEPTION_HANDLER": "apps.nucleo.api.manejador_errores",
}

REDIS_URL = env("REDIS_URL", default="redis://localhost:56379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TIMEZONE = TIME_ZONE
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "abc-semanal": {
        "task": "apps.mercaderia.tasks.recalcular_abc_todas",
        "schedule": crontab(hour=4, minute=0, day_of_week=1),
    },
    "lotes-vencidos": {"task": "apps.lotes.tasks.marcar_lotes_vencidos", "schedule": crontab(hour=0, minute=5)},
    "alertas-calidad": {"task": "apps.calidad.tasks.revisar_alertas", "schedule": crontab(minute=10)},
    "conciliacion-nocturna": {
        "task": "apps.inventario.tasks.conciliacion_nocturna",
        "schedule": crontab(hour=3, minute=0),
    },
}

OTP_TOTP_ISSUER = "Impulso Inventario"

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-responder@impulso.local")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
