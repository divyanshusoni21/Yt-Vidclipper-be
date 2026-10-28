from django.core.cache import cache
from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from utility.pagination import StandardResultsSetPagination

from .models import Blog
from .serializers import BlogSerializer


class BlogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Public, read-only ViewSet for serving blog posts on the portfolio frontend.
    Only allows 'list' and 'retrieve' actions.
    Leverages Redis caching with version-based invalidation for paginated lists.
    """
    permission_classes = (AllowAny,)
    serializer_class = BlogSerializer
    pagination_class = StandardResultsSetPagination
    lookup_field = "slug"

    def get_queryset(self):
        """
        Return published and active blog posts, prefetching associated media
        to prevent N+1 database queries.
        """
        return (
            Blog.objects.filter(is_active=True, is_published=True)
            .prefetch_related("media_files")
            .order_by("-published_at", "-created_at")
        )

    def get_serializer_context(self):
        """
        Pass context to the serializer.
        In the 'list' view, exclude the heavy markdown content to keep the response
        fast for cards and overviews; full content is provided in detail view ('retrieve').
        """
        serializerContext = super().get_serializer_context()
        if self.action == "list":
            serializerContext["exclude_fields"] = ["content"]
        return serializerContext

    def list(self, request, *args, **kwargs):
        """
        Retrieve paginated blog list from Redis cache using a versioned key.
        When a post is added, edited, or deleted, the list version increments,
        instantly invalidating all cached pages.
        """
        page = request.query_params.get("page", "1")
        pageSize = request.query_params.get("page_size", "10")

        # Read current list cache version (defaults to 1)
        listVersion = cache.get("portfolio:blogs:version", 1)
        cacheKey = f"portfolio:blogs:list:v{listVersion}:p{page}:s{pageSize}"

        cachedResponse = cache.get(cacheKey)
        if cachedResponse is not None:
            return Response(cachedResponse)

        response = super().list(request, *args, **kwargs)
        # Cache paginated response for 24 hours
        cache.set(cacheKey, response.data, timeout=86400)
        return response

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve blog detail (with replaced media URLs) from Redis cache when present,
        otherwise query, serialize with media URLs, and cache.
        """
        lookupSlug = kwargs.get(self.lookup_field)
        cacheKey = f"portfolio:blog:{lookupSlug}"
        cachedResponse = cache.get(cacheKey)
        if cachedResponse is not None:
            return Response(cachedResponse)

        response = super().retrieve(request, *args, **kwargs)
        # Cache detail for 24 hours (invalidated on Blog/BlogMedia save/delete)
        cache.set(cacheKey, response.data, timeout=86400)
        return response
