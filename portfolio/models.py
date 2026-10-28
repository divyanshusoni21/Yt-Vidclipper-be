import os

from django.db import models
from django.utils.text import slugify

from utility.mixins import UUIDMixin


def blog_banner_upload_path(instance, fileName):
    """
    Generate file upload path for blog banner images.
    Organizes banners by blog ID to prevent filename collisions.
    """
    blogId = instance.id or "misc"
    return f"portfolio/blogs/banners/{blogId}/{fileName}"


def blog_media_upload_path(instance, fileName):
    """
    Generate file upload path for blog gallery/media files.
    Groups media items inside their parent blog's folder for clean structure.
    """
    blogId = instance.blog_id or "misc"
    return f"portfolio/blogs/media/{blogId}/{fileName}"


class BlogMediaType(models.TextChoices):
    """
    Supported media types for inline blog attachments or gallery items.
    """
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"


class Blog(UUIDMixin):
    """
    Blog post model storing markdown content, metadata, and publication state.
    Inherits UUIDMixin for UUID primary key and audit timestamps (created_at, updated_at).
    """
    title = models.CharField(
        max_length=255,
        help_text="Title of the blog post",
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        blank=True,
        db_index=True,
        help_text="URL-safe unique identifier. Auto-generated from title if left blank.",
    )
    summary = models.TextField(
        blank=True,
        help_text="Short excerpt or preview description shown on portfolio cards",
    )
    content = models.TextField(
        help_text="Full blog body written in Markdown format (supports headings, bold, italic, code, etc.)",
    )
    banner_image = models.FileField(
        upload_to=blog_banner_upload_path,
        blank=True,
        null=True,
        help_text="Main cover/header image for the blog post",
    )
    video_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Optional external video link (e.g., YouTube or Vimeo embed)",
    )
    author = models.CharField(
        max_length=150,
        default="Divyanshu Soni",
        help_text="Name of the author displaying on the article",
    )
    tags = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated tags for filtering (e.g., Django, React, AI)",
    )
    reading_time_minutes = models.PositiveIntegerField(
        default=1,
        help_text="Estimated reading duration in minutes",
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Flag controlling whether the post is visible on the public portfolio",
    )
    published_at = models.DateTimeField(
        blank=True,
        null=True,
        db_index=True,
        help_text="Timestamp when the post was marked published",
    )

    class Meta:
        ordering = ("-published_at", "-created_at")
        verbose_name = "Blog"
        verbose_name_plural = "Blogs"

    def save(self, *args, **kwargs):
        """
        Auto-generate a unique URL-friendly slug from title if not explicitly provided.
        Appends an incremental number (e.g. 'my-post-1') if a collision occurs.
        """
        if not self.slug:
            baseSlug = slugify(self.title) or "blog"
            uniqueSlug = baseSlug
            counter = 1
            while Blog.objects.filter(slug=uniqueSlug).exclude(pk=self.pk).exists():
                uniqueSlug = f"{baseSlug}-{counter}"
                counter += 1
            self.slug = uniqueSlug

        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class BlogMedia(UUIDMixin):
    """
    Dedicated media model for uploading and organizing images and videos
    associated with a blog post. Media slugs allow easy inline embedding within markdown.
    """
    blog = models.ForeignKey(
        Blog,
        on_delete=models.CASCADE,
        related_name="media_files",
        help_text="Parent blog post this media item belongs to",
    )
    slug = models.SlugField(
        max_length=100,
        blank=True,
        help_text="Short identifier for markdown embedding. Auto-generated from filename or caption if left blank.",
    )
    media_file = models.FileField(
        upload_to=blog_media_upload_path,
        help_text="Uploaded media file (image or video)",
    )
    media_type = models.CharField(
        max_length=10,
        choices=BlogMediaType.choices,
        default=BlogMediaType.IMAGE,
        help_text="Distinguishes between image and video content",
    )
    caption = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional descriptive caption or accessibility alt text",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order sequence within the blog's media gallery",
    )

    class Meta:
        ordering = ("order", "created_at")
        unique_together = (("blog", "slug"),)
        verbose_name = "Blog Media"
        verbose_name_plural = "Blog Media"

    def save(self, *args, **kwargs):
        """
        Auto-generate a unique media slug from the caption or the original uploaded filename
        if no custom slug is entered. Guarantees slug uniqueness per parent blog.
        """
        if not self.slug:
            candidateText = ""
            if self.caption:
                candidateText = self.caption
            elif self.media_file and hasattr(self.media_file, "name") and self.media_file.name:
                # Strip path and file extension (e.g. 'architecture_diagram.png' -> 'architecture_diagram')
                candidateText = os.path.splitext(os.path.basename(self.media_file.name))[0]
            else:
                candidateText = self.media_type

            baseSlug = slugify(candidateText) or "media"
            uniqueSlug = baseSlug
            counter = 1

            # Prevent slug collisions among media items belonging to the same blog
            while BlogMedia.objects.filter(blog=self.blog, slug=uniqueSlug).exclude(pk=self.pk).exists():
                uniqueSlug = f"{baseSlug}-{counter}"
                counter += 1

            self.slug = uniqueSlug

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.media_type.title()} ({self.slug}) for {self.blog.title}"
