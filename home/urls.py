from .views import ClipRequestViewSet
from rest_framework.routers import DefaultRouter
from django.urls import path,include
from .auth import AuthViewSet


router = DefaultRouter()
router.register('clip-request', ClipRequestViewSet)
router.register(r'auth', AuthViewSet, basename='auth')

urlpatterns = [
    path('', include(router.urls)),
]
