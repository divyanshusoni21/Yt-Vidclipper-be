from django.apps import AppConfig


class PortfolioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portfolio"

    def ready(self):
        # Register cache invalidation signals for Blog and BlogMedia
        import portfolio.signals  # noqa: F401
