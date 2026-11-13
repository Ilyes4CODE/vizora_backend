from apps.catalog.models import Product

from .models import StoreOffer, StorePage, StoreProduct, StoreSettings

PAGES = [
    {
        "slug": "delivery",
        "sort_order": 1,
        "title_ar": "سياسة التوصيل",
        "title_en": "Delivery policy",
        "body_ar": (
            "نوصل طلبياتنا إلى 58 ولاية مع شركة Maystro Delivery، إلى المنزل أو إلى مكتب التوصيل الأقرب إليك.\n\n"
            "بعد إرسال الطلبية من الموقع يتصل بك فريقنا لتأكيدها. لا نرسل أي طلبية قبل تأكيدها معك هاتفياً.\n\n"
            "سعر التوصيل يختلف حسب الولاية ونوع التوصيل، ويظهر لك في الاستمارة قبل أن تؤكد الطلب.\n\n"
            "مدة التوصيل تختلف حسب ولايتك، وتتابع طلبيتك في كل وقت من صفحة «تتبع طلبيتي».\n\n"
            "الدفع يكون نقداً عند الاستلام، للموزّع مباشرة."
        ),
        "body_en": (
            "We deliver to all 58 wilayas with Maystro Delivery, to your home or to the nearest delivery desk.\n\n"
            "After you place an order on the website, our team calls you to confirm it. No order ships before we confirm it with you by phone.\n\n"
            "The delivery price depends on your wilaya and the delivery type, and it is shown in the form before you confirm.\n\n"
            "Delivery time depends on your wilaya, and you can follow your order at any time on the Track my order page.\n\n"
            "Payment is in cash on delivery, directly to the courier."
        ),
    },
    {
        "slug": "returns",
        "sort_order": 2,
        "title_ar": "الاستبدال والإرجاع",
        "title_en": "Exchanges and returns",
        "body_ar": (
            "إذا وصلك منتج معيب أو مختلف عن الذي طلبته، اتصل بنا خلال 48 ساعة من الاستلام وسنجد لك الحل: استبدال المنتج أو إرجاعه.\n\n"
            "يجب أن يكون المنتج في حالته الأصلية ومع كل ملحقاته.\n\n"
            "الخدمات الرقمية (التفعيل، الشحن، الاشتراكات) لا تُسترجع بعد تنفيذها، لكننا نعالج معك أي مشكلة تظهر فيها.\n\n"
            "رفض الطلبية عند الاستلام دون سبب يحمّلنا مصاريف الإرجاع، لذلك نرجو تأكيد الطلبية فقط إذا كنت متأكداً منها."
        ),
        "body_en": (
            "If you receive a faulty product or one different from what you ordered, contact us within 48 hours of delivery and we will fix it: an exchange or a return.\n\n"
            "The product must be in its original condition with all its accessories.\n\n"
            "Digital services (activation, top-ups, subscriptions) cannot be refunded once completed, but we help you with any problem that comes up.\n\n"
            "Refusing an order at delivery without a reason costs us the return fees, so please confirm your order only when you are sure."
        ),
    },
    {
        "slug": "privacy",
        "sort_order": 3,
        "title_ar": "الخصوصية",
        "title_en": "Privacy",
        "body_ar": (
            "نجمع فقط المعلومات اللازمة لتوصيل طلبيتك: الاسم، رقم الهاتف، الولاية والبلدية والعنوان.\n\n"
            "نستعمل هذه المعلومات لتأكيد الطلبية وتوصيلها، ونشاركها مع شركة التوصيل Maystro لهذا الغرض فقط.\n\n"
            "لا نبيع معلوماتك ولا نشاركها مع أي جهة أخرى.\n\n"
            "يمكنك أن تطلب حذف معلوماتك في أي وقت بالاتصال بنا."
        ),
        "body_en": (
            "We only collect what we need to deliver your order: your name, phone number, wilaya, commune and address.\n\n"
            "We use this information to confirm and deliver your order, and we share it with our courier Maystro for that purpose only.\n\n"
            "We do not sell your information or share it with anyone else.\n\n"
            "You can ask us to delete your information at any time by contacting us."
        ),
    },
    {
        "slug": "terms",
        "sort_order": 4,
        "title_ar": "شروط الاستخدام",
        "title_en": "Terms of use",
        "body_ar": (
            "بإرسال طلبية من هذا الموقع، تؤكد أن المعلومات التي كتبتها صحيحة وأنك ستكون متاحاً لاستلامها.\n\n"
            "الأسعار المعروضة بالدينار الجزائري، وسعر التوصيل يُضاف حسب الولاية.\n\n"
            "نحتفظ بحق إلغاء الطلبيات التي لا يمكن تأكيدها هاتفياً أو التي تحمل معلومات غير صحيحة.\n\n"
            "Vizora Global وكالة تسويق وخدمات رقمية، وليست بنكاً ولا جهة مُصدِرة للبطاقات."
        ),
        "body_en": (
            "By placing an order on this website, you confirm that the information you entered is correct and that you will be available to receive it.\n\n"
            "Prices are in Algerian dinars, and the delivery price is added according to your wilaya.\n\n"
            "We may cancel orders that cannot be confirmed by phone or that contain incorrect information.\n\n"
            "Vizora Global is a marketing and digital services agency, not a bank or a card issuer."
        ),
    },
]

PRODUCTS = [
    {
        "name": "كارت فيزا (كاتالوج)",
        "price": 7900,
        "slug": "visa-card",
        "sort_order": 1,
        "is_featured": True,
        "title_ar": "بطاقة فيزا (كاتالوج)",
        "title_en": "Visa card (catalogue)",
        "badge_ar": "الأكثر طلباً",
        "badge_en": "Best seller",
        "summary_ar": "بطاقة فيزا مع مرافقة كاملة في الفتح والتفعيل. عرضان حسب احتياجك.",
        "summary_en": "A Visa card with full help opening and activating it. Two offers to fit your needs.",
        "description_ar": (
            "نساعدك في فتح بطاقة الفيزا أونلاين وتفعيلها حتى تصبح جاهزة للاستعمال في الشراء والدفع عبر الإنترنت.\n\n"
            "بعد الاستلام نبقى معك: شحن الرصيد، شحن الألعاب والتطبيقات، تفعيل الاشتراكات، ومعالجة أي مشكلة تظهر لك."
        ),
        "description_en": (
            "We help you open a Visa card online and activate it so it is ready for online shopping and payments.\n\n"
            "After delivery we stay with you: balance top-ups, game and app top-ups, subscription activation, and help with any problem you run into."
        ),
        "offers": [
            {"title_ar": "العرض الأول", "title_en": "Offer 1", "price": 7900, "sort_order": 1},
            {"title_ar": "العرض الثاني", "title_en": "Offer 2", "price": 11900, "sort_order": 2},
        ],
    },
    {
        "name": "باك الترتيب والنظافة",
        "price": 2900,
        "slug": "tidy-clean-pack",
        "sort_order": 2,
        "is_featured": False,
        "title_ar": "باك الترتيب والنظافة",
        "title_en": "Tidy & clean pack",
        "badge_ar": "",
        "badge_en": "",
        "summary_ar": "باك متكامل للترتيب والنظافة.",
        "summary_en": "A complete pack for tidying and cleaning.",
        "description_ar": "",
        "description_en": "",
        "offers": [],
    },
]


def run_setup(stdout=None):
    report = []
    StoreSettings.load()
    for entry in PAGES:
        _, created = StorePage.objects.get_or_create(slug=entry["slug"], defaults={key: value for key, value in entry.items() if key != "slug"})
        report.append(f"page {entry['slug']}: {'created' if created else 'kept'}")
    for entry in PRODUCTS:
        product = Product.objects.filter(name=entry["name"]).first()
        if product is None:
            report.append(f"product {entry['slug']}: not found in the catalog, skipped")
            continue
        if product.price < 1:
            product.price = entry["price"]
            product.save(update_fields=["price"])
        fields = {key: entry[key] for key in ("slug", "sort_order", "is_featured", "title_ar", "title_en", "badge_ar", "badge_en", "summary_ar", "summary_en", "description_ar", "description_en")}
        item, created = StoreProduct.objects.get_or_create(product=product, defaults=dict(fields, is_published=True))
        if created:
            for offer in entry["offers"]:
                StoreOffer.objects.create(store_product=item, **offer)
        report.append(f"product {entry['slug']}: {'created' if created else 'kept'}")
    return report
