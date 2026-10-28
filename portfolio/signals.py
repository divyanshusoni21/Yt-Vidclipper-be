from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Blog, BlogMedia


def invalidate_blog_cache(blogSlug):
    """
    Clear individual blog detail cache and increment the list version counter in Redis.
    Bumping 'portfolio:blogs:version' instantly makes all previous paginated page keys
    obsolete in a single atomic step without needing to search or scan Redis keys.
    """
    if blogSlug:
        cache.delete(f"portfolio:blog:{blogSlug}")

    currentVersion = cache.get("portfolio:blogs:version")
    if currentVersion is None:
        cache.set("portfolio:blogs:version", 2)
    else:
        try:
            cache.incr("portfolio:blogs:version")
        except ValueError:
            cache.set("portfolio:blogs:version", currentVersion + 1)


@receiver(post_save, sender=Blog)
@receiver(post_delete, sender=Blog)
def handle_blog_cache_invalidation(sender, instance, **kwargs):
    """
    Invalidate Redis cache whenever a Blog instance is saved or deleted.
    """
    if instance.slug:
        invalidate_blog_cache(instance.slug)


@receiver(post_save, sender=BlogMedia)
@receiver(post_delete, sender=BlogMedia)
def handle_blog_media_cache_invalidation(sender, instance, **kwargs):
    """
    Invalidate Redis cache whenever an associated media file is added, edited, or removed.
    This guarantees that slug updates and replaced images immediately appear in the blog.
    """
    if instance.blog and instance.blog.slug:
        invalidate_blog_cache(instance.blog.slug)
