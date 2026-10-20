"""Empty, loopback-only UI preview. No accounts or integration results are seeded."""

import os

# Set before importing any application/configuration module (also for Flask.run).
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import secrets
from types import SimpleNamespace

from app import create_app
from database import Database
from model_catalog import CATALOG_UNAVAILABLE_MESSAGE
from user_database import UserDatabase


class DisabledAdapter:
    """Explicitly refuse model work instead of fabricating successful output."""

    def reply_with_history(self, message, history, model="deepseek"):
        raise RuntimeError("Model calls are disabled in the public preview")

    def stream_reply_with_history(self, message, history, model="deepseek"):
        raise RuntimeError("Model calls are disabled in the public preview")
        yield  # Keep the streaming interface without yielding fabricated text.

    def health_check(self, model):
        return False


class DisabledCatalog:
    """An explicit unavailable catalog with no disk or network access."""

    def load(self):
        return {
            "providers": [], "updated_at": None,
            "availability": "unavailable", "last_attempt_at": None,
            "last_success_at": None, "failure_categories": ["configuration"],
            "retryable": False, "message": CATALOG_UNAVAILABLE_MESSAGE,
        }

    def refresh(self):
        return self.load()


def disabled_dispatcher(task):
    """Preview recovery requests never enqueue work or start a worker."""
    return False


def create_demo_app():
    """Create an empty process-local app, ignoring inherited integration settings."""
    settings = SimpleNamespace(
        FLASK_SECRET_KEY=secrets.token_urlsafe(48),
        ADMIN_USERNAME="", ADMIN_PASSWORD="", ENABLE_MOCK_PAYMENTS=False,
        MOCK_PAYMENT_CALLBACK_TOKEN="", MODEL_PROVIDER_CONNECTIONS={},
        MODEL_PRICES_MICRO_POINTS={}, USE_LEGACY_API=False, MODEL_METADATA={},
        SMTP_HOST="", SMTP_FROM_EMAIL="", EMAIL_CODE_PEPPER="",
        SMS_AUTH_ENABLED=False, GATEWAY_MAIN_API_KEY="",
    )
    return create_app(
        database=Database.for_testing(), user_database=UserDatabase.for_testing(),
        adapter=DisabledAdapter(), catalog_service=DisabledCatalog(),
        password_reset_dispatcher=disabled_dispatcher,
        start_background_tasks=False, runtime_settings=settings,
    )


def main(application=None):
    application = application if application is not None else create_demo_app()
    application.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, load_dotenv=False)


if __name__ == "__main__":
    main()
