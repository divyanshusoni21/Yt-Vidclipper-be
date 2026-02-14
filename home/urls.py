from .views import ClipRequestViewSet, DownloadClipViewSet, SpeedEditViewSet, CancelRequestViewSet
from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .auth import AuthViewSet


router = DefaultRouter()
router.register('clip-request', ClipRequestViewSet, basename='clip-request')
router.register('download-clip', DownloadClipViewSet, basename='download-clip')
router.register('speed-edit', SpeedEditViewSet, basename='speed-edit')
router.register(r'auth', AuthViewSet, basename='auth')

urlpatterns = [
    path('', include(router.urls)),
    path('cancel-request/', CancelRequestViewSet.as_view(), name='cancel-request'),
]
