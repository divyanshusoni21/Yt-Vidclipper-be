from .views import ClipRequestViewSet,DownloadClipViewSet
from rest_framework.routers import DefaultRouter
from django.urls import path,include
from .auth import AuthViewSet


router = DefaultRouter()
router.register('clip-request', ClipRequestViewSet,basename='clip-request')
router.register('download-clip', DownloadClipViewSet,basename='download-clip')
router.register(r'auth', AuthViewSet, basename='auth')

urlpatterns = [
    path('', include(router.urls)),
]
