from typing import ClassVar

from django.contrib import admin

from .models import Blog, BlogMedia


class BlogMediaInline(admin.TabularInline):
    """
    Inline admin for BlogMedia to easily upload images and videos
    and assign them slug references directly within the Blog editing page.
    """
    model = BlogMedia
    extra = 1
    fields = ("slug", "media_file", "media_type", "caption", "order")
    ordering = ("order",)


@admin.register(Blog)
class BlogAdmin(admin.ModelAdmin):
    """
    Admin configuration for Blog posts.
    Includes inline media uploads, slug pre-population, and search filters.
    """
    list_display = (
        "title",
        "slug",
        "author",
        "is_published",
        "published_at",
        "reading_time_minutes",
        "created_at",
    )
    list_filter = ("is_published", "is_active", "published_at")
    search_fields = ("title", "slug", "summary", "tags", "author")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("title",)}
    inlines = (BlogMediaInline,)
    ordering = ("-created_at",)
    list_per_page = 20


@admin.register(BlogMedia)
class BlogMediaAdmin(admin.ModelAdmin):
    """
    Standalone admin for BlogMedia items for direct inspection and management.
    """
    list_display = ("id", "slug", "blog", "media_type", "caption", "order", "created_at")
    list_filter = ("media_type", "blog")
    search_fields = ("slug", "caption", "blog__title")
    ordering = ("-created_at",)
    list_per_page = 20
