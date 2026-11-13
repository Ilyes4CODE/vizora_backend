from pathlib import Path

from django.core.management.base import BaseCommand
from PIL import Image

FOLDER = Path(__file__).resolve().parents[2] / "static" / "storefront" / "illustrations"
WIDE = {"hero-1", "hero-2", "hero-3", "promo", "card-hero", "bg-shapes"}
NAMES = WIDE | {
    "feature-delivery", "feature-cod", "feature-guarantee", "step-1-order", "step-2-call", "step-3-ship", "step-4-receive",
    "thank-you", "track", "empty-cart", "not-found", "contact", "service-topup", "service-games",
}


class Command(BaseCommand):
    help = "Turn the 3D illustrations in storefront/static/storefront/illustrations into small WebP files the website uses"

    def handle(self, *args, **options):
        found = 0
        for source in sorted(FOLDER.glob("*")):
            if source.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            name = source.stem.lower()
            if name not in NAMES:
                self.stdout.write(f"skipped {source.name}: the website does not use this name")
                continue
            image = Image.open(source)
            image = image.convert("RGBA") if image.mode in ("P", "LA", "RGBA") or "transparency" in image.info else image.convert("RGB")
            limit = 1920 if name == "bg-shapes" else (1200 if name in WIDE else 512)
            image.thumbnail((limit, limit), Image.LANCZOS)
            target = FOLDER / f"{name}.webp"
            image.save(target, "WEBP", quality=84, method=6)
            found += 1
            self.stdout.write(f"{source.name} -> {target.name} {image.size[0]}x{image.size[1]} {target.stat().st_size // 1024} KB")
        missing = sorted(name for name in NAMES if not (FOLDER / f"{name}.webp").exists())
        self.stdout.write(f"{found} illustrations ready")
        if missing:
            self.stdout.write("not added yet: " + ", ".join(missing))
