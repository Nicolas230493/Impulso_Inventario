from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
SESSION_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=True)
CSRF_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
