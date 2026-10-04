#!/usr/bin/env python3
"""Download and normalize public Tutti Cafe site content into static-site data."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


SITE = "https://tutticafe.net"
ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
DATA = ROOT / "data"
DELAY_SECONDS = 0.12
TIMEOUT_SECONDS = 25
USER_AGENT = (
    "Mozilla/5.0 (compatible; TuttiCafeSiteRefresh/1.0; "
    "+https://tutticafe.net)"
)
PAGE_PATHS = {
    "home": "/",
    "menu": "/menu",
    "branches": "/branches",
    "media": "/media",
    "jobs": "/jobs",
    "about": "/about-us",
}
FAVORITE_NAMES = [
    "فطيرة سبانخ",
    "كاساديا",
    "سلطة باذنجان",
    "ورق عنب",
    "كبة باللبن",
    "كلاسيك همبرجر",
    "فاهيتا ساندويتش دجاج",
    "بيتزا مرجريتا",
    "بيني أرابياتا",
]
FALLBACK_CATEGORIES = [
    ("Hot_Coffee", "القهوة الساخنة"),
    ("Hot_Beverages", "المشروبات الساخنة"),
    ("Fresh_Juices", "العصائر الطازجة"),
    ("Soft_Drinks", "المشروبات الباردة"),
    ("Iced_Drinks", "المشروبات المثلجة"),
    ("Breakfast", "الإفطار"),
    ("Dessert_And_Cakes", "الحلويات"),
    ("Kids_Meal", "وجبات الأطفال"),
    ("Appetizers", "المقبلات"),
    ("Soups", "الشوربة"),
    ("Lebanies_Dishes", "الأطباق اللبنانية"),
    ("Salads", "السلطات"),
    ("Beef", "اللحوم"),
    ("Fish", "الأسماك"),
    ("Sandwiches", "الساندويتشات"),
    ("Chicken", "الدجاج"),
    ("Cocktails", "الكوكتيلات"),
    ("Pasta", "المكرونات"),
    ("Pizza", "البيتزا"),
]
SIZE_SUFFIXES = ("كبير", "وسط", "صغير")
SIZE_ORDER = {"صغير": 0, "وسط": 1, "كبير": 2}
MARKS = re.compile(r"[\u064b-\u065f\u0670\u0640]")
NAME_ALIASES = {
    "لاتيه سويسري": "سويس لاتيه",
    "مكسيكي كابتشنو": "مكسيكي كابتشينو",
    "كابتشينو مكسيكي": "مكسيكي كابتشينو",
}
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
PRICE_NUMBER = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)")
CALORIE_NUMBER = re.compile(r"(?<!\d)(\d+)")
SLUG_BAD = re.compile(r"[^a-zA-Z0-9_-]+")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ar,en;q=0.8"})
issues: list[str] = []
text_corrections: set[str] = set()
image_count = 0
saved_paths: set[str] = set()
downloaded_assets: dict[str, str] = {}
failed_pages: list[str] = []


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    original = unicodedata.normalize("NFC", value)
    value = MARKS.sub("", original)
    if original != value:
        text_corrections.add(f"أزيلت حركات زائدة من النص «{original.strip()}» عند تنظيفه.")
    value = value.replace("\u200f", "").replace("\u200e", "").replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    corrections = {
        "التحليه": "التحلية",
        "الروضه": "الروضة",
        "حفصه": "حفصة",
        "المملكه": "المملكة",
        "اميريكية": "أمريكية",
        "اسبريو": "إسبريسو",
        "الافطار": "الإفطار",
        "الاطفال": "الأطفال",
        "الاطباق": "الأطباق",
        "الاسماك": "الأسماك",
    }
    for wrong, correct in corrections.items():
        if wrong in value:
            text_corrections.add(f"صُحح النص «{wrong}» إلى «{correct}».")
            value = value.replace(wrong, correct)
    return value


def normalized_key(value: str) -> str:
    value = clean_text(value)
    return (
        value.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ة", "ه")
        .replace("ى", "ي")
    )


def fetch_page(path: str) -> tuple[str, BeautifulSoup] | None:
    url = urljoin(SITE, path)
    try:
        time.sleep(DELAY_SECONDS)
        response = session.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.url, BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as error:
        failed_pages.append(url)
        issues.append(f"- تعذر جلب الصفحة {url}: {error}")
        print(f"Could not fetch {url}: {error}", file=sys.stderr)
        return None


def translate_digits(value: str) -> str:
    return value.translate(ARABIC_DIGITS)


def extract_number(value: str, pattern: re.Pattern[str]) -> int | float | None:
    match = pattern.search(translate_digits(value))
    if not match:
        return None
    number = match.group(1)
    if "," in number or "." in number:
        return float(number.replace(",", "."))
    return int(number)


def optimize_image(source: Path, relative: Path, folder: str) -> Path:
    """Resize large photos and use WebP when ImageMagick can make a smaller file."""
    if source.suffix.lower() == ".webp" or folder == "" or not shutil.which("magick"):
        return relative
    resize = {
        "menu": "960x960>",
        "menu/categories": "900x900>",
        "branches": "1440x1440>",
        "slider": "1920x1920>",
    }.get(folder, "1920x1920>")
    webp_relative = relative.with_suffix(".webp")
    webp_path = PUBLIC / webp_relative
    try:
        result = subprocess.run(
            [
                "magick",
                str(source),
                "-auto-orient",
                "-resize",
                resize,
                "-strip",
                "-define",
                "webp:lossless=false",
                "-quality",
                "80",
                str(webp_path),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and webp_path.exists() and webp_path.stat().st_size < source.stat().st_size:
            source.unlink(missing_ok=True)
            return webp_relative
        webp_path.unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError):
        webp_path.unlink(missing_ok=True)
    return relative


def local_image(url: str | None, folder: str, stem: str) -> str | None:
    global image_count
    if not url:
        return None
    absolute_url = urljoin(SITE, url.strip())
    parsed = urlparse(absolute_url)
    source_path = unquote(parsed.path)
    if source_path.endswith("/img/logo/logo_inverse.png") or "logo_inverse.png" in source_path:
        return None
    if absolute_url in downloaded_assets:
        return downloaded_assets[absolute_url]
    extension = Path(source_path).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        extension = ".jpg"
    safe_stem = SLUG_BAD.sub("-", stem).strip("-_") or "image"
    safe_stem = safe_stem[:90]
    relative = Path("assets") / folder / f"{safe_stem}{extension}"
    destination = PUBLIC / relative
    try:
        time.sleep(DELAY_SECONDS)
        response = session.get(absolute_url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("image/"):
            issues.append(
                f"- ملف الصورة ليس من نوع image/*: {absolute_url} ({content_type or 'unknown'})"
            )
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        final_relative = optimize_image(destination, relative, folder)
    except requests.RequestException as error:
        issues.append(f"- تعذر تنزيل صورة {absolute_url}: {error}")
        existing_webp = PUBLIC / relative.with_suffix(".webp")
        if existing_webp.exists():
            final_relative = relative.with_suffix(".webp")
        elif destination.exists():
            final_relative = relative
        else:
            return None

    relative_name = final_relative.as_posix()
    if relative_name not in saved_paths:
        saved_paths.add(relative_name)
        image_count += 1
    local_path = relative_name
    downloaded_assets[absolute_url] = local_path
    return local_path


def image_url(image: Tag | None) -> str | None:
    if image is None:
        return None
    return image.get("data-src") or image.get("src") or image.get("data-original")


def clean_name(value: str) -> str:
    name = clean_text(value).lstrip("ًٌٍَُِّْ")
    # The live menu contains a stray leading diacritic on two tea labels.
    return name


def size_for(name: str) -> tuple[str, str | None]:
    for suffix in SIZE_SUFFIXES:
        if name.endswith(f" {suffix}"):
            return name[: -(len(suffix) + 1)].strip(), suffix
    return name, None


def category_from_tab(tab: Tag, ordinal: int) -> dict:
    category_id = clean_text(tab.get("id") or f"category-{ordinal}")
    heading = tab.find("h3", recursive=False) or tab.find("h3")
    category_name = clean_text(heading.get_text(" ", strip=True) if heading else "")
    return {
        "id": category_id,
        "name": category_name or f"قسم {ordinal}",
        "items": [],
    }


def card_image(card: Tag) -> str | None:
    img = card.select_one("img")
    return image_url(img)


def scrape_menu(soup: BeautifulSoup, home_soup: BeautifulSoup | None) -> tuple[list[dict], list[dict]]:
    menu_items: list[dict] = []
    categories: list[dict] = []
    favorite_cards: list[tuple[str, str | None]] = []
    favorite_heading = next(
        (
            heading
            for heading in soup.find_all(["h2", "h3"])
            if normalized_key(heading.get_text(" ", strip=True)) == normalized_key("المفضلة")
        ),
        None,
    )
    if favorite_heading:
        container = favorite_heading.find_parent(class_="our-menu")
        if container:
            for card in container.select(".card"):
                heading = card.find("h3")
                if heading:
                    favorite_cards.append(
                        (clean_name(heading.get_text(" ", strip=True)), card_image(card))
                    )

    for category_index, tab in enumerate(soup.select(".tab-pane[role='tabpanel']"), start=1):
        category = category_from_tab(tab, category_index)
        category_id = category["id"]
        category_key = normalized_key(category["name"])
        raw_variants: OrderedDict[str, list[dict]] = OrderedDict()

        for item_index, card in enumerate(tab.select(".menu-item-info"), start=1):
            info_heading = card.find("h3")
            if not info_heading:
                continue
            full_name = clean_name(info_heading.get_text(" ", strip=True))
            base_name, size_label = size_for(full_name)
            canonical_name = NAME_ALIASES.get(base_name, base_name)
            if canonical_name != base_name:
                issues.append(
                    f"- اختلاف/خطأ في تسمية «{full_name}»؛ وُحّد الاسم إلى «{canonical_name}» لتجميع الأحجام."
                )
                base_name = canonical_name
            price_text = clean_text(
                (card.select_one(".meal-price") or card.find("p", string=re.compile("السعر"))).get_text(
                    " ", strip=True
                )
                if card.select_one(".meal-price") or card.find("p", string=re.compile("السعر"))
                else ""
            )
            calories_node = card.select_one(".meal-calories")
            calories_text = clean_text(calories_node.get_text(" ", strip=True) if calories_node else "")
            price = extract_number(price_text, PRICE_NUMBER)
            calories = extract_number(calories_text, CALORIE_NUMBER)

            outer = card.find_parent(class_=re.compile(r"\bcol-"))
            img = outer.select_one(".menu-item-img img") if outer else None
            source_image = image_url(img)
            description = ""
            for paragraph in card.find_all("p"):
                if paragraph is calories_node or "meal-price" in paragraph.get("class", []):
                    continue
                text = clean_text(paragraph.get_text(" ", strip=True))
                if text:
                    text = re.sub(r"السعر(?:ات الحرارية)?\s*:\s*[\d٠-٩۰-۹.,\s]+.*$", "", text).strip()
                    if text:
                        description = text
                        break

            meal_match = re.search(r"/meals/(\d+)/", urlparse(urljoin(SITE, source_image or "")).path)
            meal_id = meal_match.group(1) if meal_match else f"{category_id}-{item_index}"
            local_path = local_image(source_image, "menu", f"meal-{meal_id}") if source_image else None
            if source_image and "logo_inverse.png" in source_image:
                issues.append(f"- صورة عنصر «{full_name}» كانت شعاراً مؤقتاً؛ عُرض مكانها عنصر بديل.")
            elif not source_image:
                issues.append(f"- صورة مفقودة للصنف «{full_name}» في قسم «{category['name']}».")
            elif not local_path:
                issues.append(f"- تعذر حفظ الصورة المحلية للصنف «{full_name}».")

            if price == 0:
                issues.append(
                    f"- سعر الصنف «{full_name}» في «{category['name']}» يساوي 0 في المصدر؛ سُجل السعر null."
                )
                price = None
            elif price is None:
                issues.append(
                    f"- السعر غير موجود للصنف «{full_name}» في «{category['name']}»؛ سُجل السعر null."
                )
            if calories is None:
                issues.append(
                    f"- السعرات الحرارية غير موجودة للصنف «{full_name}» في «{category['name']}»."
                )
            if not description:
                issues.append(
                    f"- لا يوجد وصف للصنف «{full_name}» في «{category['name']}»؛ تُرك الوصف فارغاً."
                )

            base_key = normalized_key(base_name)
            raw_variants.setdefault(
                base_key,
                [],
            ).append(
                {
                    "name": base_name,
                    "size_label": size_label or "عادي",
                    "price": price,
                    "calories": calories,
                    "image": local_path,
                    "description": description,
                    "source_id": meal_id,
                }
            )

        for base_key, variants in raw_variants.items():
            unique: list[dict] = []
            seen_size_labels: set[str] = set()
            for variant in variants:
                if variant["size_label"] in seen_size_labels:
                    issues.append(
                        f"- تكرار اسم/حجم «{variant['name']} {variant['size_label']}» في قسم «{category['name']}»."
                    )
                    continue
                seen_size_labels.add(variant["size_label"])
                unique.append(variant)
            unique.sort(key=lambda variant: SIZE_ORDER.get(variant["size_label"], 3))
            primary = next((variant for variant in unique if variant["image"]), unique[0])
            if len(unique) > 1 and any(v["size_label"] == "عادي" for v in unique):
                issues.append(
                    f"- توجد نسخ مكررة محتملة من «{primary['name']}» بأحجام ومن دون حجم في «{category['name']}»؛ عُرضت كخيارات منفصلة."
                )
            is_favorite = any(
                normalized_key(favorite_name) == base_key
                for favorite_name, _ in favorite_cards
            )
            item = {
                "id": f"meal-{primary['source_id']}",
                "category_id": category_id,
                "category_name_ar": category["name"],
                "name": primary["name"],
                "description": primary["description"],
                "image": primary["image"],
                "sizes": [
                    {
                        "label": variant["size_label"],
                        "price": variant["price"],
                        "calories": variant["calories"],
                    }
                    for variant in unique
                ],
                "is_favorite": is_favorite,
            }
            category["items"].append(item)
            menu_items.append(item)
        categories.append({key: value for key, value in category.items() if key != "items"})

    # The home/menu favorites are a source-verified list even when a full menu
    # category does not expose that dish or its price.
    canonical_names = {normalized_key(item["name"]): item for item in menu_items}
    for favorite_index, favorite_name in enumerate(FAVORITE_NAMES, start=1):
        match = canonical_names.get(normalized_key(favorite_name))
        source_image = next(
            (
                image
                for name, image in favorite_cards
                if normalized_key(name) == normalized_key(favorite_name)
            ),
            None,
        )
        if match:
            match["is_favorite"] = True
            if not match["image"] and source_image:
                match["image"] = local_image(source_image, "menu", f"favorite-{favorite_index}")
            continue
        local_path = local_image(source_image, "menu", f"favorite-{favorite_index}") if source_image else None
        missing_item = {
            "id": f"favorite-{favorite_index}",
            "category_id": None,
            "category_name_ar": "المفضلة",
            "name": favorite_name,
            "description": "",
            "image": local_path,
            "sizes": [{"label": "عادي", "price": None, "calories": None}],
            "is_favorite": True,
        }
        menu_items.append(missing_item)
        issues.append(
            f"- الصنف المفضل «{favorite_name}» ظاهر في المصدر كوجبة مفضلة فقط؛ لا توجد له بيانات سعر/سعرات موثقة في أقسام المنيو."
        )

    if home_soup:
        home_category_images = {}
        for link in home_soup.select("a[href*='/menu']"):
            img = link.select_one("img")
            if not img:
                continue
            src = image_url(img)
            if not src or "/img/menu/category/" not in src:
                continue
            match = re.search(r"/category/(\d+)/", urlparse(urljoin(SITE, src)).path)
            if not match:
                continue
            source_id = match.group(1)
            name_node = link.find(["strong", "b"])
            category_name = clean_text(
                name_node.get_text(" ", strip=True) if name_node else link.get_text(" ", strip=True)
            )
            home_category_images[normalized_key(category_name)] = local_image(
                src, "menu/categories", f"category-{source_id}"
            )
        for category in categories:
            image = home_category_images.get(normalized_key(category["name"]))
            if image:
                category["image"] = image

    return menu_items, categories


def scrape_branches(soup: BeautifulSoup) -> list[dict]:
    branches = []
    for index, card in enumerate(soup.select(".job-item .card"), start=1):
        heading = card.select_one(".branch-title") or card.find("h3")
        if not heading:
            continue
        name = clean_text(heading.get_text(" ", strip=True))
        address_node = card.select_one(".store-address")
        address = clean_text(address_node.get_text(" ", strip=True) if address_node else "")
        address = re.sub(r"^.*?حي|^.*?الرياض", lambda match: match.group(0), address)
        phone_link = card.select_one("a[href^='tel:']")
        phone = ""
        if phone_link:
            phone = re.sub(r"\D", "", phone_link.get("href", "").replace("tel:", ""))
            if not phone:
                phone = re.sub(r"\D", "", phone_link.get_text(" ", strip=True))
        branch_type_node = card.select_one(".branch-type")
        branch_type = clean_text(branch_type_node.get_text(" ", strip=True) if branch_type_node else "")
        source_image = card_image(card)
        branch_match = re.search(r"/branches/(\d+)/main/", urlparse(urljoin(SITE, source_image or "")).path)
        branch_id = branch_match.group(1) if branch_match else str(index)
        local_path = local_image(source_image, "branches", f"branch-{branch_id}") if source_image else None
        city = "القصيم" if "القصيم" in (name + " " + address) else "الرياض"
        map_query = quote(f"{name}، {address}")
        if not address:
            issues.append(f"- العنوان غير موجود للفرع «{name}».")
        if not phone:
            issues.append(f"- رقم الهاتف غير موجود للفرع «{name}».")
        if not local_path:
            issues.append(f"- صورة مفقودة أو تعذر تنزيلها للفرع «{name}».")
        branches.append(
            {
                "id": f"branch-{branch_id}",
                "name": name,
                "type": branch_type,
                "city": city,
                "address": address,
                "phone": phone,
                "image": local_path,
                "map_url": f"https://www.google.com/maps/search/?api=1&query={map_query}",
            }
        )
    return branches


def extract_sections(soup: BeautifulSoup) -> dict[str, str]:
    sections = {}
    for heading in soup.find_all(["h2", "h3", "h4"]):
        label = clean_text(heading.get_text(" ", strip=True))
        if not label:
            continue
        container = heading.find_parent(["section", "div"])
        if not container:
            continue
        paragraphs = [clean_text(p.get_text(" ", strip=True)) for p in container.find_all("p")]
        paragraphs = [text for text in paragraphs if text]
        if paragraphs and label not in sections:
            sections[label] = " ".join(paragraphs[:3])
    return sections


def scrape_site(
    pages: dict[str, tuple[str, BeautifulSoup] | None],
    branches: list[dict],
    menu_items: list[dict],
    categories: list[dict],
) -> dict:
    home = pages.get("home")
    about = pages.get("about")
    media = pages.get("media")
    jobs = pages.get("jobs")
    home_soup = home[1] if home else BeautifulSoup("", "html.parser")
    about_soup = about[1] if about else BeautifulSoup("", "html.parser")
    sections = extract_sections(about_soup)
    source_story = sections.get("قصتنا", "")
    if "أكثر من 13 فروع" in source_story:
        cleaned_story = source_story.replace(
            "حتى توسعنا وأصبح لدينا أكثر من 13 فروع فى المملكة", ""
        ).strip(" ،،-")
        if cleaned_story == source_story:
            cleaned_story = re.sub(
                r"\s*أكثر من 13 فروع فى المملكة.*$",
                "",
                source_story,
            ).strip(" ،،-")
        sections["قصتنا"] = cleaned_story
        issues.append(
            "- صفحة «من نحن» تذكر أكثر من 13 فرعاً، وهو ما يتعارض مع الفروع السبعة المدرجة حالياً؛ أُسقط الرقم من نص القصة المعروض."
        )

    logo = None
    for img in home_soup.select("img"):
        source = image_url(img)
        if source and urlparse(urljoin(SITE, source)).path.endswith("/img/logo.png"):
            logo = local_image(source, "", "logo")
            break
    # Keep the actual official logo local, but retain a graceful fallback if it is absent.
    if not logo:
        logo_source = urljoin(SITE, "/img/logo.png")
        logo = local_image(logo_source, "", "logo")

    slider_images = []
    for index, img in enumerate(home_soup.select("img"), start=1):
        source = image_url(img)
        if not source or "/img/slider/" not in urlparse(urljoin(SITE, source)).path:
            continue
        local_path = local_image(source, "slider", f"home-slider-{index}")
        if local_path:
            slider_images.append({"image": local_path, "alt": clean_text(img.get("alt", ""))})

    gallery_images = []
    if media:
        for index, img in enumerate(media[1].select("img"), start=1):
            source = image_url(img)
            if not source:
                continue
            local_path = local_image(source, "slider", f"media-gallery-{index}")
            if local_path:
                gallery_images.append(
                    {"id": f"media-{index}", "image": local_path, "alt": clean_text(img.get("alt", ""))}
                )

    # The public site exposes directly accessible PDF files on its home page.
    pdf_labels = {
        "23": "منيو الشباب - محلات الشارع",
        "24": "منيو الشباب - محلات المول",
        "25": "منيو العائلات - محلات الشارع",
        "26": "منيو العائلات - محلات المول",
        "27": "منيو فرع الروضة",
        "28": "منيو إضافي 28",
        "29": "منيو إضافي 29",
        "30": "منيو إضافي 30",
        "31": "منيو أبها",
    }
    pdfs = []
    for anchor in home_soup.select("a[href*='/viewPDF/']"):
        source_label = clean_text(anchor.get_text(" ", strip=True))
        href = urljoin(SITE, anchor.get("href", ""))
        pdf_id_match = re.search(r"/viewPDF/(\d+)", href)
        pdf_id = pdf_id_match.group(1) if pdf_id_match else ""
        if href and href not in {item["url"] for item in pdfs}:
            pdfs.append(
                {
                    "label": pdf_labels.get(pdf_id, f"منيو إضافي {pdf_id}"),
                    "url": href,
                }
            )

    mailto = ""
    tel = ""
    for soup in [home_soup, about_soup, media[1] if media else None, jobs[1] if jobs else None]:
        if not soup:
            continue
        if not mailto:
            anchor = soup.select_one("a[href^='mailto:']")
            if anchor:
                mailto = anchor.get("href", "").replace("mailto:", "").split("?")[0]
        if not tel:
            anchor = soup.select_one("a[href^='tel:']")
            if anchor:
                tel = re.sub(r"\D", "", anchor.get("href", "").replace("tel:", ""))

    socials = {"instagram": "https://www.instagram.com/tutticafee/", "twitter": "https://twitter.com/tutticafe"}
    for anchor in home_soup.select("a[href]"):
        href = anchor.get("href", "")
        if "instagram.com" in href:
            socials["instagram"] = urljoin(SITE, href)
        elif "twitter.com" in href or "x.com" in href:
            socials["twitter"] = urljoin(SITE, href)

    jobs_message = ""
    jobs_positions = []
    if jobs:
        jobs_soup = jobs[1]
        jobs_message = clean_text(jobs_soup.get_text(" ", strip=True))
        if "لا توجد وظائف شاغرة" in jobs_message:
            jobs_message = "لا توجد وظائف شاغرة الآن."
        for card in jobs_soup.select(".job-item, .job-card, .job"):
            heading = card.find(["h2", "h3", "h4"])
            if heading:
                jobs_positions.append({"title": clean_text(heading.get_text(" ", strip=True))})
    if not jobs:
        jobs_message = "لا تتوفر تفاصيل الوظائف حالياً."

    tagline = clean_text(
        next(
            (
                paragraph.get_text(" ", strip=True)
                for paragraph in about_soup.find_all("p")
                if "معمل حلويات" in paragraph.get_text(" ", strip=True)
            ),
            "",
        )
    )
    if not tagline:
        tagline = (
            "تفرد مشروباتنا بطابع خاص ومذاق مميز، دائماً ما نبحث عن الأعلى جودة ولذلك قمنا "
            "بإنشاء معمل حلويات خاص بنا لصناعة الحلويات الطازجة والمعدة يومياً تقديراً لثقة عملائنا بنا."
        )
    tagline = tagline.replace("تقديرا", "تقديراً")

    # Contact and HQ data below is supplied in the user's approved fallback brief.
    main_phone = tel or "0114520184"
    email = mailto or "info@tutticafe.net"
    return {
        "brand_name": "توتي كافيه",
        "tagline": tagline,
        "description": clean_text(
            next(
                (
                    meta.get("content", "")
                    for meta in home_soup.select("meta[name='description']")
                    if meta.get("content")
                ),
                tagline,
            )
        ),
        "logo": logo,
        "contact": {"phone": main_phone, "email": email},
        "headquarters": {
            "address": "طريق الملك عبدالعزيز - حي الربيع - الرياض - المملكة العربية السعودية",
            "map_url": "https://www.google.com/maps/search/?api=1&query=24.795354,46.656003",
        },
        "social": socials,
        "about": {
            "sections": sections,
            "story": sections.get("قصتنا", ""),
            "mission": sections.get("مهمتنا", ""),
            "vision": sections.get("رؤيتنا", ""),
            "values": sections.get("قيمنا", ""),
            "products": tagline,
        },
        "jobs": {"message": jobs_message, "positions": jobs_positions},
        "media": gallery_images,
        "slider": slider_images,
        "pdfs": pdfs,
        "categories": categories,
        "scraped_pages": {
            key: urljoin(SITE, path) for key, path in PAGE_PATHS.items()
        },
        "source_notes": [
            "المحتوى المستخرج من صفحات توتي كافيه العامة؛ الأسعار والأصناف المفقودة لا تُستكمل بالتخمين.",
            "حقائق الاتصال والعنوان الاجتماعي الاحتياطي مأخوذة من موجز المالك المرفق.",
        ],
    }


def fallback_branches() -> list[dict]:
    facts = [
        ("فرع التحلية", "أفراد", "الرياض", "حي العليا، شارع التحلية، بجوار أبل بيز، الرياض", "0114634397"),
        ("فرع التحلية 2", "أفراد وعائلات", "الرياض", "حي العليا، شارع التحلية، بجوار ألو كار، الرياض", "0114169595"),
        ("فرع الربيع", "أفراد", "الرياض", "حي الربيع، طريق الملك عبدالعزيز، بين مدارس ومستشفى المملكة، الرياض", "0112756265"),
        ("فرع حي النخيل", "أفراد", "الرياض", "حي النخيل، طريق الأمير تركي الأول، الرياض", "0114537500"),
        ("فرع حي الروضة", "أفراد وعائلات", "الرياض", "حي الروضة، شارع حفصة بنت عمر، بجانب بنك سامبا، الرياض", "0112080205"),
        ("فرع الحمراء مول", "أفراد وعائلات", "الرياض", "الحمراء مول، طريق الملك عبدالله، الرياض", "0556776273"),
        ("فرع النخيل بلازا - القصيم", "أفراد وعائلات", "القصيم", "النخيل بلازا، القصيم", "0551810083"),
    ]
    return [
        {
            "id": f"branch-fallback-{index}",
            "name": name,
            "type": branch_type,
            "city": city,
            "address": address,
            "phone": phone,
            "image": None,
            "map_url": f"https://www.google.com/maps/search/?api=1&query={quote(f'{name}، {address}')}",
        }
        for index, (name, branch_type, city, address, phone) in enumerate(facts, start=1)
    ]


def fallback_menu() -> tuple[list[dict], list[dict]]:
    categories = [
        {"id": category_id, "name": category_name}
        for category_id, category_name in FALLBACK_CATEGORIES
    ]
    items = [
        {
            "id": f"favorite-fallback-{index}",
            "category_id": None,
            "category_name_ar": "المفضلة",
            "name": name,
            "description": "",
            "image": None,
            "sizes": [{"label": "عادي", "price": None, "calories": None}],
            "is_favorite": True,
        }
        for index, name in enumerate(FAVORITE_NAMES, start=1)
    ]
    return items, categories


def prune_unreferenced_scraped_images(
    menu_items: list[dict], branches: list[dict], site: dict
) -> None:
    """Remove stale scraper-generated photos while keeping every JSON-referenced asset."""
    referenced = set()
    for image in (
        [item.get("image") for item in menu_items]
        + [branch.get("image") for branch in branches]
        + [item.get("image") for item in site.get("media", [])]
        + [item.get("image") for item in site.get("slider", [])]
        + [item.get("image") for item in site.get("categories", [])]
    ):
        if image:
            referenced.add(Path(image).as_posix())

    for folder in ("menu", "branches", "slider"):
        asset_root = PUBLIC / "assets" / folder
        if not asset_root.exists():
            continue
        for asset in asset_root.rglob("*"):
            if asset.is_file() and asset.relative_to(PUBLIC).as_posix() not in referenced:
                asset.unlink()
        for directory in sorted(
            (path for path in asset_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    pages: dict[str, tuple[str, BeautifulSoup] | None] = {}
    for key, path in PAGE_PATHS.items():
        pages[key] = fetch_page(path)

    menu_page = pages.get("menu")
    home_page = pages.get("home")
    menu_items: list[dict] = []
    categories: list[dict] = []
    if menu_page:
        menu_items, categories = scrape_menu(
            menu_page[1],
            home_page[1] if home_page else None,
        )
    else:
        issues.append("- تعذر استخراج المنيو؛ استُخدمت الأقسام والمفضلة المعروفة فقط، من دون اختراع أسعار.")
        menu_items, categories = fallback_menu()

    branches: list[dict] = []
    if pages.get("branches"):
        branches = scrape_branches(pages["branches"][1])
    if not branches:
        issues.append("- تعذر استخراج الفروع كاملة من الموقع؛ استُخدمت حقائق الفروع الواردة في موجز المالك.")
        branches = fallback_branches()

    site = scrape_site(pages, branches, menu_items, categories)
    write_json(DATA / "menu.json", menu_items)
    write_json(DATA / "branches.json", branches)
    write_json(DATA / "site.json", site)
    prune_unreferenced_scraped_images(menu_items, branches, site)

    issues.extend(
        f"- {correction}"
        for correction in sorted(text_corrections)
        if f"- {correction}" not in issues
    )
    issues_text = [
        "# ملاحظات استخراج بيانات توتي كافيه",
        "",
        "هذه الملاحظات مبنية على الصفحات العامة التي جرى فحصها. لم تُخترع أسعار أو أصناف.",
        "",
        "## ملخص",
        f"- الأقسام المستخرجة: {len(categories)}.",
        f"- الأصناف بعد جمع أحجام الصنف الواحد: {len(menu_items)}.",
        f"- الفروع المستخرجة: {len(branches)}.",
        f"- صور المصدر التي جرى تنزيلها: {image_count}.",
        f"- الصفحات التي تعذر جلبها: {len(failed_pages)}.",
        "",
        "## تفاصيل الملاحظات",
    ]
    if issues:
        issues_text.extend(issues)
    else:
        issues_text.append("- لم تُسجّل ملاحظات إضافية.")
    (ROOT / "issues.md").write_text("\n".join(issues_text) + "\n", encoding="utf-8")

    summary = {
        "categories": len(categories),
        "items": len(menu_items),
        "branches": len(branches),
        "images_downloaded": image_count,
        "failed_pages": failed_pages,
        "issues": len(issues),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if pages.get("menu") and pages.get("branches") else 1


if __name__ == "__main__":
    raise SystemExit(main())