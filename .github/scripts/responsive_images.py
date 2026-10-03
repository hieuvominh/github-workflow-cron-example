"""Small deterministic WebP variants for crawler-managed article images."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


RESPONSIVE_WIDTHS = (480, 768, 1280, 1600)


def make_responsive_variants(
    image_data,
    *,
    quality=84,
    max_bytes=15_000_000,
    max_width=1600,
    max_height=1600,
):
    """Return ascending (width, bytes, extension) variants without upscaling.

    Animated GIFs remain a single GIF so animation is preserved. Every other
    supported raster format is normalized to WebP. The final variant is the
    largest supported width and should remain the canonical CMS image URL.
    """
    format_extensions = {"GIF": "gif", "JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
    try:
        with Image.open(BytesIO(image_data)) as source:
            image_format = (source.format or "").upper()
            extension = format_extensions.get(image_format)
            if not extension:
                return None
            if getattr(source, "is_animated", False):
                if len(image_data) > max_bytes:
                    return None
                return [(source.width, image_data, extension)]

            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            image.thumbnail(
                (max_width, max_height),
                Image.Resampling.LANCZOS,
            )
            source_width, source_height = image.size
            targets = [width for width in RESPONSIVE_WIDTHS if width < source_width]
            targets.append(source_width)
            variants = []
            for width in targets:
                height = max(1, round(source_height * width / source_width))
                resized = image if width == source_width else image.resize(
                    (width, height), Image.Resampling.LANCZOS
                )
                output = BytesIO()
                resized.save(output, format="WEBP", quality=quality, method=6)
                data = output.getvalue()
                if len(data) > max_bytes:
                    return None
                variants.append((width, data, "webp"))
            return variants
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        return None
