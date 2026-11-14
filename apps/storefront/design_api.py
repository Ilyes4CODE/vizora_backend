import re

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit

from . import layouts
from .design import TEMPLATES, DesignError, schema
from .models import DesignImage, Layout, LayoutVersion, StoreProduct
from .serializers import ImageUploadSerializer, optimized_image, public_base
from .templatetags.store import illustration
from .public import ILLUSTRATIONS

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LANGS = ("ar", "en")


def design_failure(error):
    return Response({"detail": error.message, "code": "design_invalid", "errors": {error.path or "draft": [error.message]}}, status=400)


def media_url(request, path):
    return request.build_absolute_uri(path)


def layout_url(layout):
    base = public_base()
    if layout.kind == Layout.Kind.LANDING:
        return f"{base}{'/en' if layout.lang == 'en' else ''}/l/{layout.slug}/"
    return f"{base}/"


def summary(layout, stats=None):
    product = layout.store_product
    return {
        "id": layout.pk,
        "kind": layout.kind,
        "slug": layout.slug,
        "name": layout.name,
        "lang": layout.lang,
        "product": {"id": product.pk, "title": product.title_ar, "slug": product.slug, "is_published": product.is_published} if product else None,
        "is_live": layout.is_live,
        "has_changes": layout.has_changes,
        "published_at": layout.published_at,
        "updated_at": layout.updated_at,
        "url": layout_url(layout),
        "stats": stats,
    }


def detail(layout):
    stats = layouts.landing_stats([layout])[layout.pk] if layout.kind == Layout.Kind.LANDING else None
    return {**summary(layout, stats), "draft": layout.draft, "published": layout.published}


class DesignView(APIView):
    section = "products"

    def get(self, request):
        layouts.ensure("site")
        layouts.ensure("home")
        rows = list(Layout.objects.select_related("store_product"))
        landing_rows = [row for row in rows if row.kind == Layout.Kind.LANDING]
        stats = layouts.landing_stats(landing_rows)
        order = {Layout.Kind.SITE: 0, Layout.Kind.HOME: 1, Layout.Kind.LANDING: 2}
        rows.sort(key=lambda row: (order[row.kind], -row.created_at.timestamp()))
        products = StoreProduct.objects.all()
        return Response(
            {
                "schema": schema(),
                "preview": {"base": public_base(), "token": layouts.design_token()},
                "layouts": [summary(row, stats.get(row.pk)) for row in rows],
                "products": [{"id": item.pk, "title": item.title_ar, "slug": item.slug, "is_published": item.is_published} for item in products],
            }
        )


class DesignTokenView(APIView):
    section = "products"

    def post(self, request):
        token = layouts.rotate_token()
        audit.record("design_preview_rotated", "غيّر رابط معاينة التصميم", request=request)
        return Response({"token": token})


class LayoutCreateView(APIView):
    section = "products"

    def post(self, request):
        data = request.data
        name = str(data.get("name") or "").strip()[:120]
        lang = data.get("lang") if data.get("lang") in LANGS else "ar"
        template = data.get("template") if data.get("template") in TEMPLATES else "quick"
        slug = str(data.get("slug") or "").strip().lower()
        errors = {}
        if len(name) < 2:
            errors["name"] = ["اكتب اسماً للصفحة"]
        product = StoreProduct.objects.filter(pk=data.get("product")).first()
        if product is None:
            errors["product"] = ["اختر المنتج"]
        if slug and (not SLUG.fullmatch(slug) or len(slug) > 60):
            errors["slug"] = ["الرابط حروف لاتينية صغيرة وأرقام وشرطة"]
        elif slug and (Layout.objects.filter(slug=slug).exists() or slug in ("site", "home", "new", "preview")):
            errors["slug"] = ["هذا الرابط مستعمل"]
        if errors:
            return Response({"detail": next(iter(errors.values()))[0], "code": "validation_error", "errors": errors}, status=400)
        layout = layouts.create_landing(name, product, lang, template, slug or None)
        audit.record("landing_created", f"أنشأ صفحة الهبوط {layout.name}", request=request)
        return Response(detail(layout), status=status.HTTP_201_CREATED)


class LayoutDetailView(APIView):
    section = "products"

    def get(self, request, pk):
        return Response(detail(get_object_or_404(Layout, pk=pk)))

    def patch(self, request, pk):
        layout = get_object_or_404(Layout, pk=pk)
        data = request.data
        errors = {}
        if "draft" in data:
            try:
                layouts.save_draft(layout, data["draft"])
            except DesignError as error:
                return design_failure(error)
        if layout.kind == Layout.Kind.LANDING:
            if "name" in data:
                name = str(data.get("name") or "").strip()[:120]
                if len(name) < 2:
                    errors["name"] = ["اكتب اسماً للصفحة"]
                else:
                    layout.name = name
            if "slug" in data:
                slug = str(data.get("slug") or "").strip().lower()
                if not SLUG.fullmatch(slug) or len(slug) > 60 or slug in ("site", "home", "new", "preview"):
                    errors["slug"] = ["الرابط حروف لاتينية صغيرة وأرقام وشرطة"]
                elif Layout.objects.filter(slug=slug).exclude(pk=layout.pk).exists():
                    errors["slug"] = ["هذا الرابط مستعمل"]
                else:
                    layout.slug = slug
            if "lang" in data:
                if data["lang"] not in LANGS:
                    errors["lang"] = ["اختر العربية أو الإنجليزية"]
                else:
                    layout.lang = data["lang"]
            if "product" in data:
                product = StoreProduct.objects.filter(pk=data.get("product")).first()
                if product is None:
                    errors["product"] = ["اختر المنتج"]
                else:
                    layout.store_product = product
            if "is_live" in data:
                live = bool(data["is_live"])
                if live and layout.published is None:
                    errors["is_live"] = ["انشر الصفحة أولاً"]
                else:
                    layout.is_live = live
        if errors:
            return Response({"detail": next(iter(errors.values()))[0], "code": "validation_error", "errors": errors}, status=400)
        layout.save()
        return Response(detail(layout))

    def delete(self, request, pk):
        layout = get_object_or_404(Layout, pk=pk)
        if layout.kind != Layout.Kind.LANDING:
            return Response({"detail": "لا يمكن حذف هذه الصفحة", "code": "not_allowed"}, status=409)
        audit.record("landing_deleted", f"حذف صفحة الهبوط {layout.name}", request=request)
        layout.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LayoutPublishView(APIView):
    section = "products"

    def post(self, request, pk):
        layout = get_object_or_404(Layout, pk=pk)
        try:
            layouts.publish(layout, request.user)
        except DesignError as error:
            return design_failure(error)
        audit.record("design_published", f"نشر {layout.name}", request=request)
        return Response(detail(layout))


class LayoutDiscardView(APIView):
    section = "products"

    def post(self, request, pk):
        return Response(detail(layouts.discard(get_object_or_404(Layout, pk=pk))))


class LayoutDuplicateView(APIView):
    section = "products"

    def post(self, request, pk):
        layout = get_object_or_404(Layout, pk=pk, kind=Layout.Kind.LANDING)
        return Response(detail(layouts.duplicate(layout)), status=status.HTTP_201_CREATED)


class LayoutVersionsView(APIView):
    section = "products"

    def get(self, request, pk):
        layout = get_object_or_404(Layout, pk=pk)
        return Response(
            [
                {"id": version.pk, "created_at": version.created_at, "user": version.created_by.full_name if version.created_by else ""}
                for version in layout.versions.select_related("created_by")
            ]
        )


class LayoutRestoreView(APIView):
    section = "products"

    def post(self, request, pk, version):
        layout = get_object_or_404(Layout, pk=pk)
        entry = get_object_or_404(LayoutVersion, pk=version, layout=layout)
        try:
            layouts.restore(layout, entry)
        except DesignError as error:
            return design_failure(error)
        return Response(detail(layout))


class DesignImagesView(APIView):
    section = "products"

    def get(self, request):
        uploaded = [
            {"id": entry.pk, "url": entry.image.url, "preview": media_url(request, entry.image.url), "group": "uploaded"}
            for entry in DesignImage.objects.all()[:200]
            if entry.image
        ]
        art = []
        for name in ILLUSTRATIONS:
            url = illustration(name)
            if url:
                art.append({"id": name, "url": url, "preview": media_url(request, url), "group": "illustrations"})
        products = []
        for item in StoreProduct.objects.prefetch_related("images"):
            for picture in item.images.all():
                if picture.image:
                    products.append({"id": f"p{picture.pk}", "url": picture.image.url, "preview": media_url(request, picture.image.url), "group": "products"})
        return Response(uploaded + products + art)

    def post(self, request):
        serializer = ImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stem = f"design-{timezone.now().strftime('%Y%m%d%H%M%S%f')}"
        content, (width, height) = optimized_image(serializer.validated_data["image"], stem)
        entry = DesignImage(width=width, height=height)
        entry.image.save(content.name, content, save=True)
        return Response(
            {"id": entry.pk, "url": entry.image.url, "preview": media_url(request, entry.image.url), "group": "uploaded", "width": width, "height": height},
            status=status.HTTP_201_CREATED,
        )
