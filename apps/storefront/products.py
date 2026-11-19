from django.utils import timezone
from django.utils.text import slugify

from .models import StoreImage, StoreProduct
from .serializers import optimized_image


def free_slug(base):
    base = (base or "product")[:70].strip("-") or "product"
    slug = base
    index = 2
    while StoreProduct.objects.filter(slug=slug).exists():
        slug = f"{base}-{index}"
        index += 1
    return slug


def add_to_store(product, title_en="", publish=True):
    existing = StoreProduct.objects.filter(product=product).first()
    if existing is not None:
        return existing
    item = StoreProduct.objects.create(
        product=product,
        slug=free_slug(slugify(title_en) or f"product-{product.pk}"),
        title_ar=product.name,
        title_en=title_en,
        is_published=publish and product.price > 0,
    )
    if product.image:
        product.image.open("rb")
        try:
            content, (width, height) = optimized_image(product.image, f"{item.slug}-{timezone.now().strftime('%Y%m%d%H%M%S%f')}")
        finally:
            product.image.close()
        picture = StoreImage(store_product=item, width=width, height=height, alt_ar=product.name, alt_en=title_en)
        picture.image.save(content.name, content, save=True)
    return item
