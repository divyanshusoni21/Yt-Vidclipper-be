from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BlogViewSet

portfolioRouter = DefaultRouter()
portfolioRouter.register("blogs", BlogViewSet, basename="blogs")

urlpatterns = [
    path("", include(portfolioRouter.urls)),
]
