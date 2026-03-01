from .views import ClipRequestViewSet, DownloadClipViewSet, SpeedEditViewSet, CancelRequestViewSet, CleanupOldFilesViewSet
from rest_framework.routers import DefaultRouter
from django.urls import path, include


router = DefaultRouter()
router.register('clip-request', ClipRequestViewSet, basename='clip-request')
router.register('download-clip', DownloadClipViewSet, basename='download-clip')
router.register('speed-edit', SpeedEditViewSet, basename='speed-edit')

urlpatterns = [
    path('', include(router.urls)),
    path('cancel-request/', CancelRequestViewSet.as_view(), name='cancel-request'),
    path('cleanup-old-files/', CleanupOldFilesViewSet.as_view(), name='cleanup-old-files'),
]
