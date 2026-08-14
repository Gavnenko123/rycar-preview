"""
Монитор цен для GitHub Actions.

Адаптированная версия monitor_v3.py для запуска в GitHub Actions runner:
  - Без SQLite (результаты сразу в JSON/CSV)
  - Сохраняет в docs/results.json + docs/results_alerts.csv + docs/results_all.csv
  - Логи в docs/scan_log.txt
  - Принимает запросы как аргумент или использует значения по умолчанию

Запуск:
  python3 scripts/monitor_actions.py "Ноутбук,Смартфон Samsung,Видеокарта RTX"
"""
import sys
import os
import re
import time
import json
import csv
import random
import logging
import statistics
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# Пути относительно корня репозитория (запуск из корня)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
os.makedirs(DOCS_DIR, exist_ok=True)

# Лог в файл + консоль
log_path = os.path.join(DOCS_DIR, "scan_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_path, mode='w', encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ===========================================================================
# МОДЕЛИ
# ===========================================================================
@dataclass
class Product:
    name: str
    url: str
    price: float
    site: str
    category: str = ""
    search_query: str = ""
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class MispriceAlert:
    name: str
    url: str
    site: str
    category: str
    search_query: str
    current_price: float
    median_price: float
    expected_min_price: float
    discount_vs_median_pct: float
    misprice_score: float
    severity: str
    reason: str


# ===========================================================================
# КАТЕГОРИИ И ФИЛЬТРЫ
# ===========================================================================
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    'laptop': ['ноутбук', 'laptop', 'macbook', 'thinkpad', 'ideapad', 'vivobook',
               'victus', 'predator', 'nitro', 'legion', 'tuf gaming'],
    'pc': [' ПК ', 'комп\'ютер', 'компьютер', 'десктоп', 'desktop', 'моноблок',
           'all-in-one', ' aio ', 'робоча станція', 'рабочая станція'],
    'phone': ['смартфон', 'телефон', 'smartphone', 'iphone', 'samsung galaxy',
              'xiaomi', 'redmi note', 'redmi ', 'poco', 'realme', 'honor ',
              'oneplus', 'pixel '],
    'gpu': ['відеокарта', 'видеокарта', 'videocard', 'video card', 'graphics card',
            'geforce', 'radeon', 'rtx ', 'rx ', 'gtx ', 'arc '],
    'cpu': ['процесор', 'процессор', 'processor', 'ryzen', 'core i', 'core ultra',
            'core duo', 'pentium', 'celeron', 'athlon', 'epyc'],
    'motherboard': ['материнська плата', 'материнская плата', 'motherboard', 'плата '],
    'monitor': ['монітор', 'монитор', 'monitor'],
    'tablet': ['планшет', 'планшет', 'tablet', 'ipad'],
}

MIN_PRICE_BY_CATEGORY: Dict[str, float] = {
    'laptop': 5000, 'pc': 8000, 'phone': 2000, 'gpu': 3000,
    'cpu': 2500, 'motherboard': 2000, 'monitor': 2500, 'tablet': 3000, 'other': 1500,
}

ACCESSORY_KEYWORDS = [
    'сумка', 'чохол', 'підставка', 'зарядний', 'зарядка', 'адаптер', 'кабель',
    'миша', 'клавіатура', 'навушники', 'стікер', 'плівка', 'скло', 'тримач',
    'кронштейн', 'пульт', 'блок живлення', 'стенд', 'охолодження', 'кулер',
    'вентилятор', 'кріплення', 'переходник', 'перехідник', 'хаб', 'розгалужувач',
    'підсилювач', 'ремінець', 'чехол', 'подставка', 'зарядное', 'мышь',
    'наушники', 'стикер', 'пленка', 'стекло', 'держатель', 'блок питания',
    'охлаждение', 'кулер', 'вентилятор', 'крепление', 'переходник', 'хаб',
    'разветвитель', 'усилитель', 'ремешок', 'карта памяти', 'флешка',
    'usb-накопитель', 'ssd накопитель', 'hdd', 'жесткий диск', 'аккумулятор',
    'батарея', 'акумулятор', 'ремкомплект', 'захисне', 'захисна', 'защитное',
]


def detect_category(name: str) -> str:
    n = name.lower()
    for cat in ['gpu', 'cpu', 'motherboard', 'laptop', 'pc',
                'phone', 'tablet', 'monitor']:
        for kw in CATEGORY_KEYWORDS[cat]:
            if kw in n:
                return cat
    return 'other'


def is_accessory(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in ACCESSORY_KEYWORDS)


# ===========================================================================
# MISPRICE-ДЕТЕКТОР
# ===========================================================================
class MispriceDetector:
    RATIO_THRESHOLD = 0.20
    ZSCORE_THRESHOLD = -3.0
    MIN_ABSOLUTE_PRICE = 100

    def analyze(self, products: List[Product]) -> List[MispriceAlert]:
        alerts: List[MispriceAlert] = []
        by_cat: Dict[str, List[Product]] = {}
        for p in products:
            cat = p.category or detect_category(p.name)
            p.category = cat
            by_cat.setdefault(cat, []).append(p)

        stats = {}
        for cat, items in by_cat.items():
            if len(items) < 2:
                continue
            min_price = MIN_PRICE_BY_CATEGORY.get(cat, 1500)
            main_items = [
                p for p in items
                if not is_accessory(p.name) and p.price >= min_price
            ]
            if len(main_items) < 2:
                main_items = [p for p in items if not is_accessory(p.name)]
            prices = [p.price for p in main_items]
            if len(prices) < 2:
                continue

            median = statistics.median(prices)
            mad = statistics.median([abs(pr - median) for pr in prices])
            sigma = 1.4826 * mad if mad > 0 else (statistics.stdev(prices) if len(prices) > 1 else 0)
            stats[cat] = {
                'count': len(items), 'main_count': len(main_items),
                'median': median, 'mad': mad, 'sigma': sigma,
            }
            logger.info(f"[{cat}] товаров={len(items)}, основных={len(main_items)}, "
                        f"медиана={median:,.0f}, MAD={mad:,.0f}, σ≈{sigma:,.0f}")

            expected_min = 0.30 * median
            for p in items:
                if p.price < self.MIN_ABSOLUTE_PRICE:
                    continue
                if is_accessory(p.name):
                    continue
                z = (p.price - median) / sigma if sigma > 0 else 0.0
                ratio = p.price / median if median > 0 else 1.0
                discount_pct = (1 - ratio) * 100
                reasons = []
                if p.price < self.RATIO_THRESHOLD * median:
                    reasons.append(f"ціна < {int(self.RATIO_THRESHOLD*100)}% від медіани "
                                   f"({p.price:,.0f} < {self.RATIO_THRESHOLD*median:,.0f}₴)")
                if z < self.ZSCORE_THRESHOLD and sigma > 0:
                    reasons.append(f"z-score={z:.2f} (викид)")
                if p.price < expected_min:
                    reasons.append(f"ціна нижче очікуваного мінімуму "
                                   f"({p.price:,.0f} < {expected_min:,.0f}₴)")
                if not reasons:
                    continue
                score = 0.0
                if median > 0:
                    score += min(50, max(0, (1 - ratio) * 60))
                if sigma > 0 and z < 0:
                    score += min(40, abs(z) * 10)
                if p.price < min_price:
                    score += 10
                score = min(100, round(score, 1))
                severity = ('critical' if score >= 70 else 'high' if score >= 50
                            else 'medium' if score >= 30 else 'low')
                alerts.append(MispriceAlert(
                    name=p.name, url=p.url, site=p.site, category=cat,
                    search_query=p.search_query, current_price=p.price,
                    median_price=median, expected_min_price=expected_min,
                    discount_vs_median_pct=round(discount_pct, 1),
                    misprice_score=score, severity=severity,
                    reason='; '.join(reasons),
                ))
        alerts.sort(key=lambda a: (-a.misprice_score, a.current_price))
        return alerts, stats


# ===========================================================================
# СКРАПЕРЫ (Playwright)
# ===========================================================================
JS_EXTRACT_COMFY = r"""
() => {
    const out = [];
    const cards = document.querySelectorAll('.product-tile-catalog');
    cards.forEach((c, i) => {
        if (i >= 30) return;
        const nameEl = c.querySelector('a.product-tile-title__name, a[class*="__title"]');
        if (!nameEl) return;
        const name = nameEl.textContent.trim();
        if (!name) return;
        const oldEl = c.querySelector('.product-tile-price__old-value');
        const mainPriceEl = c.querySelector('.product-tile-price');
        let price = null;
        if (mainPriceEl) {
            const txt = mainPriceEl.textContent.replace(/\s+/g, ' ').trim();
            if (oldEl) {
                const nums = txt.match(/(\d[\d\s]*)\s*₴\s*(\d[\d\s]*)/);
                if (nums) price = nums[2].replace(/\s/g,'');
            } else {
                const m = txt.match(/(\d[\d\s]*)\s*₴/);
                if (m) price = m[1].replace(/\s/g,'');
            }
        }
        if (price && parseFloat(price) > 0) {
            out.push({name, url: nameEl.href, price: parseFloat(price)});
        }
    });
    return out;
}
"""

JS_EXTRACT_CITRUS = r"""
() => {
    const out = [];
    const cards = document.querySelectorAll('[class*="MainProductCard"]');
    cards.forEach((c, i) => {
        if (i >= 40) return;
        const links = Array.from(c.querySelectorAll('a'));
        const nameLink = links.find(a => a.href && a.textContent.trim().length > 5);
        if (!nameLink) return;
        const name = nameLink.textContent.trim().replace(/\s*\d+.*$/, '').slice(0, 120);
        if (!name) return;
        let price = null;
        const priceEls = c.querySelectorAll('[class*="price"],[class*="Price"]');
        for (const e of priceEls) {
            const cls = e.className.toLowerCase();
            if (cls.includes('old') || cls.includes('currency')) continue;
            const t = e.textContent.replace(/\s+/g,'').trim();
            const m = t.match(/(\d[\d\s]*)₴/);
            if (m) { price = m[1].replace(/\s/g,''); break; }
        }
        if (price && parseFloat(price) > 0) {
            out.push({name, url: nameLink.href, price: parseFloat(price)});
        }
    });
    const seen = new Set();
    return out.filter(p => { if (seen.has(p.url)) return false; seen.add(p.url); return true; });
}
"""


class PlaywrightScraper:
    def __init__(self, site_name, search_url_template, extract_js,
                 wait_selector=None, post_load_wait=5500):
        self.site_name = site_name
        self.search_url_template = search_url_template
        self.extract_js = extract_js
        self.wait_selector = wait_selector
        self.post_load_wait = post_load_wait

    def scrape(self, page, query: str) -> List[Product]:
        url = self.search_url_template.format(q=quote_plus(query))
        logger.info(f"[{self.site_name}] {url}")
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
        except Exception as e:
            logger.warning(f"[{self.site_name}] goto failed: {e}")
            return []
        if self.wait_selector:
            try:
                page.wait_for_selector(self.wait_selector, timeout=15000)
            except Exception:
                pass
        page.wait_for_timeout(self.post_load_wait)
        try:
            for _ in range(4):
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(800)
        except Exception:
            pass
        title = page.title()
        if any(b in title for b in ['Attention Required', 'Just a moment',
                                    'Access denied', 'Доступ обмежено']):
            logger.warning(f"[{self.site_name}] БЛОКИРОВКА: {title}")
            return []
        try:
            raw = page.evaluate(self.extract_js)
        except Exception as e:
            logger.error(f"[{self.site_name}] extract error: {e}")
            return []
        products = [Product(name=r['name'], url=r['url'], price=float(r['price']),
                            site=self.site_name, search_query=query)
                    for r in raw]
        logger.info(f"[{self.site_name}] найдено: {len(products)}")
        return products


SCRAPERS = [
    PlaywrightScraper("Comfy", "https://comfy.ua/ua/search/?q={q}", JS_EXTRACT_COMFY,
                      '.product-tile-catalog', 5500),
    PlaywrightScraper("Citrus", "https://citrus.ua/uk/search?query={q}", JS_EXTRACT_CITRUS,
                      '[class*="MainProductCard"]', 6500),
]


def scan(queries: List[str]) -> List[Product]:
    all_products: List[Product] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                  '--disable-dev-shm-usage']
        )
        stealth = Stealth()
        for query in queries:
            logger.info(f"=== Поиск: '{query}' ===")
            for scraper in SCRAPERS:
                ctx = browser.new_context(
                    user_agent='Mozilla/5.0 (X11; Linux x86_64) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) '
                               'Chrome/131.0.0.0 Safari/537.36',
                    locale='uk-UA', viewport={'width': 1366, 'height': 900},
                    extra_http_headers={'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.7'},
                )
                stealth.apply_stealth_sync(ctx)
                page = ctx.new_page()
                try:
                    products = scraper.scrape(page, query)
                    for p in products:
                        p.category = detect_category(p.name)
                    all_products.extend(products)
                except Exception as e:
                    logger.error(f"[{scraper.site_name}] {e}")
                ctx.close()
                time.sleep(random.uniform(0.8, 1.6))
            time.sleep(random.uniform(1.0, 2.0))
        browser.close()
    return all_products


# ===========================================================================
# ТОЧКА ВХОДА
# ===========================================================================
def main():
    if len(sys.argv) > 1:
        queries = [q.strip() for q in sys.argv[1].split(",") if q.strip()]
    else:
        queries = ["Ноутбук", "Смартфон Samsung", "Видеокарта RTX"]

    logger.info(f"Запросы: {queries}")
    t0 = time.time()
    products = scan(queries)
    elapsed = time.time() - t0
    logger.info(f"Собрано {len(products)} товаров за {elapsed:.1f} сек")

    detector = MispriceDetector()
    alerts, stats = detector.analyze(products)
    logger.info(f"Найдено {len(alerts)} misprice-алертов")

    by_site: Dict[str, int] = {}
    by_cat: Dict[str, int] = {}
    for p in products:
        by_site[p.site] = by_site.get(p.site, 0) + 1
        by_cat[p.category] = by_cat.get(p.category, 0) + 1

    report = {
        'queries': queries,
        'scan_time_utc': datetime.now(timezone.utc).isoformat(),
        'scan_duration_sec': round(elapsed, 1),
        'total_products_scanned': len(products),
        'by_site': by_site,
        'by_category': by_cat,
        'category_stats': {k: {**v, 'median': round(v['median'], 2),
                                'mad': round(v['mad'], 2),
                                'sigma': round(v['sigma'], 2)}
                           for k, v in stats.items()},
        'misprice_alerts_count': len(alerts),
        'critical_count': sum(1 for a in alerts if a.severity == 'critical'),
        'high_count': sum(1 for a in alerts if a.severity == 'high'),
        'alerts': [asdict(a) for a in alerts],
        'all_products': [
            {'name': p.name, 'url': p.url, 'price': p.price,
             'site': p.site, 'category': p.category, 'search_query': p.search_query}
            for p in products
        ],
    }

    # Сохраняем результаты в docs/ (для GitHub Pages)
    with open(os.path.join(DOCS_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    with open(os.path.join(DOCS_DIR, "results_alerts.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(['severity', 'misprice_score', 'category', 'site',
                    'current_price_uah', 'median_price_uah',
                    'discount_vs_median_pct', 'name', 'reason', 'url'])
        for a in alerts:
            w.writerow([a.severity, a.misprice_score, a.category, a.site,
                        a.current_price, a.median_price,
                        a.discount_vs_median_pct, a.name, a.reason, a.url])

    with open(os.path.join(DOCS_DIR, "results_all.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(['category', 'site', 'search_query', 'price_uah', 'name', 'url'])
        for p in sorted(products, key=lambda x: (x.category, x.price)):
            w.writerow([p.category, p.site, p.search_query, p.price, p.name, p.url])

    logger.info(f"Результаты сохранены в {DOCS_DIR}/")
    print(f"\n=== ИТОГ: {len(products)} товаров, {len(alerts)} алертов ===")
    if alerts:
        for a in alerts[:5]:
            print(f"  [{a.severity}] {a.current_price:.0f}₴ vs {a.median_price:.0f}₴ — {a.name[:50]}")


if __name__ == "__main__":
    main()
