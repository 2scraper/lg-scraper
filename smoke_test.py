#!/usr/bin/env python3
"""
smoke_test.py
--------------
The offline suite: one file of plain functions with inline fixtures, no
pytest, no conftest, no fixtures directory. `tests/test_smoke.py` wraps this
as a single pytest test so `pytest` works as an entry point without a second
copy of the checks.

    python3 smoke_test.py

It must pass with NO engine library installed at all — every
`import playwright_scraper` / `puppeteer_scraper` / `selenium_scraper` is
guarded and the skip is recorded and printed. CI's `engine-smoke` job
installs each engine in its own virtualenv and fails if any group reports
skipped, because "skipped, engine absent" reads identically to a real import
error.

**The fixtures are real captures, trimmed and verified.**

PAGE_FIXTURE_HTML is two grid cards, the site's own `#categoryFilterForm`,
the unfilled template row and two cards of the recommendation rail, cut from
a plain-HTTP capture of `/ru/televisions` (2026-09-21) with the `<svg>` and
`<script>` subtrees removed. Before it was committed, every column of both
rows was compared against the same rows parsed from the untrimmed 364 KB
capture and found identical. The template row and the rail are IN the
fixture on purpose: they are what the parser has to throw away.

API_FIXTURE_JSON is the matching two records of the catalogue API's answer
for the same category, with its own `totalCount`.

SUPPORT_FIXTURE_HTML is the shape of the reCAPTCHA v3 widget that
`/ru/support/email-to-ceo` renders, with the sitekey replaced by an obvious
placeholder of the same shape: the tests need the structure, not the site's
live key.
"""

import ast
import csv
import inspect
import io
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import asdict
from types import SimpleNamespace

import page_flow
import product_parser
import proxy_pool
import env_config
import output_writer
import captcha_solver
import catalog_client
from output_writer import (EXIT_BLOCKED, EXIT_NO_PRODUCTS, EXIT_PARTIAL, Product,
                           dedupe_by_sku, finish_run, save, write_csv)
from product_parser import (category_from_url, page_url, parse_products)

REPO = os.path.dirname(os.path.abspath(__file__))

# Engine modules are optional: the suite has to pass with none of the driver
# libraries installed. What is NOT optional is that each engine imports its
# driver at MODULE level — see check_engine_imports_driver_at_module_level.
ENGINES = {}
SKIPPED_GROUPS = []
for _name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
    try:
        ENGINES[_name] = __import__(_name)
    except ImportError as exc:
        SKIPPED_GROUPS.append(f"{_name} ({exc.name or exc})")

PASSED = []
FAILED = []


def check(label, condition):
    (PASSED if condition else FAILED).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    return bool(condition)


def eq(label, actual, expected):
    ok = actual == expected
    if not ok:
        label = f"{label} (got {actual!r}, expected {expected!r})"
    return check(label, ok)


PAGE_FIXTURE_HTML = r"""<!DOCTYPE html><html><head><title>Телевизоры LG | LG Russia</title><link rel="canonical" href="https://www.lg.com/ru/televisions"/><link rel="alternate" hreflang="ru-ua" href="https://www.lg.com/ua/televisions"/><link rel="alternate" hreflang="ru-ru" href="https://www.lg.com/ru/televisions"/><link rel="stylesheet" href="/lg5-common-gp/css/common.css"/><link rel="preload" href="/lg5-common-gp/js/part0.js"/><link rel="preload" href="/lg5-common-gp/js/part1.js"/><link rel="preload" href="/lg5-common-gp/js/part2.js"/><link rel="preload" href="/lg5-common-gp/js/part3.js"/><link rel="preload" href="/lg5-common-gp/js/part4.js"/><link rel="preload" href="/lg5-common-gp/js/part5.js"/><link rel="preload" href="/lg5-common-gp/js/part6.js"/><link rel="preload" href="/lg5-common-gp/js/part7.js"/></head><body><form action="/ru/mkt/ajax/category/retrieveCategoryProductList" class="filter-box" data-price-sync-url="/ru/mkt/ajax/priceSync/retrievePlpPriceSyncList" id="categoryFilterForm" method="post" novalidate="">
<input id="v" name="v" type="hidden" value="1"/> <!-- PJTPLP-10 GILS CACHE 버전 정보 -->
<input name="categoryId" type="hidden" value="CT20206007"/>
<input name="modelStatusCode" type="hidden" value="ACTIVE"/>
<input name="bizType" type="hidden" value="B2C"/>
<input name="viewAll" type="hidden" value=""/>
<input name="filterFlag" type="hidden" value="Y"/>
<input data-desktop="9" data-mobile="6" name="length" type="hidden" value="9"/>
<input name="sort" type="hidden" value=""/>
<input name="page" type="hidden" value="1"/>
<input name="pagePosition" type="hidden"/>
<input id="pdfDownloadFile" name="pdfDownloadFile" type="hidden" value="Скачать файл pdf"/>
<input id="productFicheDownload" name="productFicheDownload" type="hidden" value="Product Fiche"/>
<input id="rsProductFicheDownload" name="rsProductFicheDownload" type="hidden" value="component-RsProductFicheDownload"/>
<input id="productOldFicheDownload" name="productOldFicheDownload" type="hidden" value="component-old-productFicheDownload"/>
<input id="productNewFicheDownload" name="productNewFicheDownload" type="hidden" value="component-new-productFicheDownload"/>
<input id="pdfDownloadFileUk" name="pdfDownloadFileUk" type="hidden" value="component-pdfDownloadFile-UK"/>
<input id="pdfDownloadFileEu" name="pdfDownloadFileEu" type="hidden" value="component-pdfDownloadFile-EU"/>
<input id="specMsg" name="specMsg" type="hidden" value="Energy Information Update In Progress"/>
<input id="sortByFlag" name="sortByFlag" type="hidden" value="Y"/>
<input class="addtocart-datelayer-use-flag" type="hidden" value="addtocart-datelayer-use-flag"/>
<input class="currency-code" type="hidden" value="RUB"/>
<span class="guide-for-user">Установите флажок, чтобы уточнить результаты поиска нет?</span>
<div class="float-filter-result">
<span class="result-number">203</span> из 203 Результаты
					</div>
<div class="filter-result">
<a class="link-text ico-right" href="#">РЕЗУЛЬТАТЫ ФИЛЬТРА (<span class="result-number">203</span>)</a>
</div>
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Категория</legend>
<div class="title">
<strong aria-hidden="true">Категория</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT05654254_FV15917469_FV15917469">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">OLED</span> <span class="filter-cnt">(37)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654254_FV65278046_FV65278046">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">QNED</span> <span class="filter-cnt">(65)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654254_FV15917468_FV15917468">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">NanoCell</span> <span class="filter-cnt">(22)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654254_FV65011694_FV65011694">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Большие диагонали</span> <span class="filter-cnt">(32)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654254_FV15917467_FV15917467">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">UHD 4K</span> <span class="filter-cnt">(28)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654254_FV15917466_FV15917466">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">LED Full HD / HD</span> <span class="filter-cnt">(2)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Диагональ</legend>
<div class="title">
<strong aria-hidden="true">Диагональ</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361931_FV23670315">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">32 дюйма и ниже</span> <span class="filter-cnt">(3)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361932_FV53593105">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">42-48 дюймов</span> <span class="filter-cnt">(30)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361933_FV23670328">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">50 дюймов</span> <span class="filter-cnt">(23)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361934_FV23670331">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">55 дюймов</span> <span class="filter-cnt">(40)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361935_FV23670336">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">65 дюймов</span> <span class="filter-cnt">(42)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361936_FV23670338">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">70-75 дюймов</span> <span class="filter-cnt">(22)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0361937_FV65271392">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">77-85 дюймов</span> <span class="filter-cnt">(22)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0362625_FV65494680">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">86 - 98 дюймов</span> <span class="filter-cnt">(17)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05654268_FTV0362624_FV65573059">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">100  дюймов и выше</span> <span class="filter-cnt">(3)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Разрешение</legend>
<div class="title">
<strong aria-hidden="true">Разрешение</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT06522117_FTV0361449_FV65564730">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">4K</span> <span class="filter-cnt">(198)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Ultimate Game Experience</legend>
<div class="title">
<strong aria-hidden="true">Ultimate Game Experience</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT06522118_FTV0361464_FV65564747">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">ALLM</span> <span class="filter-cnt">(198)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522118_FTV0361465_FV65564749">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">AMD FreeSync</span> <span class="filter-cnt">(96)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522118_FTV0361466_FV65564752">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">NVIDIA G-Sync</span> <span class="filter-cnt">(53)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522118_FTV0361467_FV65564748">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Оптимизатор игр</span> <span class="filter-cnt">(201)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">True Cinema Experience</legend>
<div class="title">
<strong aria-hidden="true">True Cinema Experience</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT06522119_FTV0361479_FV65564787">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Dolby Vision</span> <span class="filter-cnt">(77)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522119_FTV0361475_FV65564788">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Dolby Atmos</span> <span class="filter-cnt">(68)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522119_FTV0361476_FV65564782">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Режиссёрский режим</span> <span class="filter-cnt">(198)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Год производства</legend>
<div class="title">
<strong aria-hidden="true">Год производства</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT06522120_FV65564807_FV65564807">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">2023</span> <span class="filter-cnt">(12)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522120_FV65564806_FV65564806">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">2024</span> <span class="filter-cnt">(50)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522120_FV65572822_FV65572822">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">2025</span> <span class="filter-cnt">(71)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Operating System (OS)</legend>
<div class="title">
<strong aria-hidden="true">Operating System (OS)</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT06522512_FV65572949_FV65572949">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">webOS 23</span> <span class="filter-cnt">(12)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522512_FV65572948_FV65572948">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">webOS 24</span> <span class="filter-cnt">(50)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06522512_FV65572950_FV65572950">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">webOS 25</span> <span class="filter-cnt">(71)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Особенности</legend>
<div class="title">
<strong aria-hidden="true">Особенности</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT05710923_FT06502201_FV65572818">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">LG ThinQ</span> <span class="filter-cnt">(201)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05710923_FT05710924_FV50156898">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">SMART TV</span> <span class="filter-cnt">(0)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05710923_FT05710930_FV65572819">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Пульт Magic Remote</span> <span class="filter-cnt">(63)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT05710923_FT05710932_FV50153064">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">Wi-Fi</span> <span class="filter-cnt">(0)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<fieldset class="option-box open"><!-- start filter -->
<legend class="sr-only">Частота обновления экрана</legend>
<div class="title">
<strong aria-hidden="true">Частота обновления экрана</strong>
<a aria-expanded="true" class="btn-list" href="#" role="button">toggle</a>
</div>
<div class="option-list">
<ul class="list-box-type1">
<li>
<label class="checkbox-box checkbox-sm" for="FT06519808_FTV0355284_FV65528571">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">60 Гц</span> <span class="filter-cnt">(103)</span></span>
</label>
</li>
<li>
<label class="checkbox-box checkbox-sm" for="FT06519808_FTV0355282_FV65528572">

<span class="checkbox-btn"></span>
<span class="text"><span class="name">120 Гц</span> <span class="filter-cnt">(40)</span></span>
</label>
</li>
</ul>
</div>
</fieldset><!-- end filter -->
<script>
                        var dragbarVal = {
};
                        if(dragbarVal.priceOption){
                            dragbarVal.priceOption = JSON.parse(JSON.stringify(dragbarVal.priceOption).replace(/\&nbsp;/g, " "));
                        }
					</script>
<div class="etc-box">
<button aria-controls="resultAppendTarget" class="link-text" type="reset">Очистить все фильтры</button>
<a class="link-text ico-right" href="https://www.lg.com/ru/televisions/discontinued" target="_self">Продукты, снятые с производства</a>
</div>
</form><div class="result-box"><div class="product-list-box js-model-switcher"><div class="item js-model" itemscope="" itemtype="http://schema.org/Product">
<meta alt="83-дюймовый телевизор Smart TV Wallpaper TV LG OLED evo AI W6 4K 2026 года" content="/ru/images/televisions/md07610675/md07610675-350x350.jpg" itemprop="image"/>
<div class="sr-only" itemprop="offers" itemscope="" itemtype="http://schema.org/Offer">
<link href="/ru/televisions/lg-oled83w69la" itemprop="url"/>
<meta alt="83-дюймовый телевизор Smart TV Wallpaper TV LG OLED evo AI W6 4K 2026 года" content="/ru/images/televisions/md07610675/md07610675-350x350.jpg" itemprop="image"/>
<meta content="RUB" itemprop="priceCurrency"/>
<meta content="https://schema.org/NewCondition" itemprop="itemCondition"/>
<meta content="0" itemprop="price"/>
</div>
<div class="tag-content">
<p class="d-none" data-user-type="ALL"><span>Новинка</span></p>
</div>
<div class="pd-sideInfo" data-wish-model-id="MD07610675" data-wish-url="/ru/mkt/ajax/product/retrieveWishListCntAndWishOn">
<div class="pd-share share-common">
<button class="sns" type="button"><span class="visually-hidden">SNS Share</span></button>
<div class="list">
<div class="sns-inner">
<strong>Поделиться этой информацией. Вы можете рассказать о понравившемся товаре своим друзьям.</strong>
<ul class="sns-share" data-get-url="https://www.lg.com/common/shorturl/getShortUrl.lgajax" data-param-name="longUrl">
<li><a class="share-vk" data-adobe-copy-icon="vk" data-adobe-tracking-wish="Y" data-image="https://www.lg.com/ru/images/televisions/md07610675/md07610675-350x350.jpg" data-link-area="social_share-share_layer_popup" data-link-name="vk" data-page-event="plp_share_copyicon" data-title="Телевизоры LG - купить ТВ ЛДЖИ по выгодной цене, все модели, отзывы | LG Russia" data-url="https://www.lg.com/ru/televisions/lg-oled83w69la" href="#" title="VK, Открыть новое окно">VK</a></li>
<li><a class="share-ok" data-adobe-copy-icon="ok" data-adobe-tracking-wish="Y" data-image="https://www.lg.com/ru/images/televisions/md07610675/md07610675-350x350.jpg" data-link-area="social_share-share_layer_popup" data-link-name="ok" data-page-event="plp_share_copyicon" data-title="Телевизоры LG - купить ТВ ЛДЖИ по выгодной цене, все модели, отзывы | LG Russia" data-url="https://www.lg.com/ru/televisions/lg-oled83w69la" href="#" title="OK, Открыть новое окно">OK</a></li>
<li><a class="article-link" data-adobe-copy-icon="urlcopy" data-adobe-tracking-wish="Y" data-copy-url="https://www.lg.com/ru/televisions/lg-oled83w69la" data-page-event="plp_share_copyicon" data-toggle="modal" href="#share-complete">Copy Url</a></li>
</ul>
<div class="plp-snsClose">
<button class="btn btn-primary btn-sm" type="button">Закрыть</button>
</div>
</div>
</div>
</div>
</div>
<a aria-hidden="true" class="visual" data-adobe-modelname="OLED83W69LA" data-adobe-salesmodelcode="OLED83W69LA" data-adobe-salessuffixcode="ARUG" data-link-area="product_list-model_list" data-link-name="oled83w69la" href="/ru/televisions/lg-oled83w69la" tabindex="-1">
<img alt="Вид спереди на телевизор LG OLED evo AI W6 Wallpaper TV, выпущенный в 2026 году, демонстрирует элегантный дизайн Wallpaper, а динамичная абстрактная композиция волнообразных градиентов ярких цветов пл1" class="pc js-thumbnail-loop lazyload" data-img-list="/ru/images/televisions/md07610675/md07610675-350x350.jpg,/ru/images/televisions/md07610675/thumbnail/350-1.jpg,/ru/images/televisions/md07610675/thumbnail/350-2.jpg,/ru/images/televisions/md07610675/thumbnail/350-3.jpg,/ru/images/televisions/md07610675/thumbnail/350-4.jpg,/ru/images/televisions/md07610675/thumbnail/350-5.jpg" data-src="/ru/images/televisions/md07610675/md07610675-350x350.jpg" itemprop="image"/>
<img alt="Вид спереди на телевизор LG OLED evo AI W6 Wallpaper TV, выпущенный в 2026 году, демонстрирует элегантный дизайн Wallpaper, а динамичная абстрактная композиция волнообразных градиентов ярких цветов пл2" class="lazyload mobile" data-src="/ru/images/televisions/md07610675/md07610675-260x260.jpg" itemprop="image"/>
</a>
<div class="model-group">
<div aria-label="Размер экрана" class="inner" role="radiogroup">
<a aria-checked="true" class="size active" data-href="MD07610675" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">83"</a>
<a aria-checked="false" class="size" data-href="MD08807920" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">77"</a>
</div>
</div>
<div class="products-info" data-model-id="MD07610675"> <div class="pd-group2">
<p class="model-name" itemprop="name"><a data-adobe-tracking-wish="Y" data-link-area="product_list-model_list" data-link-name="oled83w69la" data-page-event="plp_modelname" href="/ru/televisions/lg-oled83w69la">83-дюймовый телевизор Smart TV Wallpaper TV LG OLED evo AI W6 4K 2026 года</a></p>
<div class="sku" data-model-name="OLED83W69LA">
<a data-adobe-tracking-wish="Y" data-link-area="product_list-model_list" data-link-name="oled83w69la" data-page-event="plp_modelname" href="/ru/televisions/lg-oled83w69la">
										OLED83W69LA
										</a>
<a class="copy-model-name" href="#" title="Скопировать название модели">Скопировать название модели</a>
</div>
</div>
<div class="rating rating-ru-box" data-adobe-tracking-wish="Y" data-page-event="plp_star_link_click"><a href="/ru/televisions/lg-oled83w69la#pdp_review"><span data-shoppilot="oled83w69la"></span></a></div>
<div class="label-list">
<div class="label-inner">
<ul>
<li>
<p>Благодаря толщине всего 9 мм дизайн Wallpaper привносит эстетичность в окружающее пространство</p>
</li>
<li>
<p>Технология беспроводной передачи данных 4K 165 Гц для безупречного качества изображения</p>
</li>
<li>
<p>Технология сверхсияющего цвета в телевизорах LG OLED нового поколения для нового уровня качества изображения</p>
</li>
</ul>
</div>
<button type="button"><span class="visually-hidden">Open</span></button>
</div>
<div class="promotion-tag-text promotionTagText box-type d-none" data-promotion-tag-text-flag="N" data-promotion-tag-text-pdp-link="promotion_tag_text_pdp_link" data-promotion-tag-text-pdp-link-id-login="promotion_tag_text_pdp_link_in_login">
<a class="promotion-tag-text-link" href="#"><p class="info-txt d-none"><span>Extra coupon alleen voor leden</span></p></a>
</div>
<div class="model-button">
<div class="button">
<a class="btn btn-outline-secondary btn-sm add-to-cart" data-link-area="product_list-model_list" data-link-name="add_to_cart" data-model-id="MD07610675" href="#" role="button">добавить в корзину</a>
<a class="btn btn btn-primary btn-sm where-to-buy active" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="QNED_TV" data-buname-two="TV" data-category-name="TV" data-content-id="MD07610675" data-link-area="product_list-model_list" data-link-name="where_to_buy" data-model-id="MD07610675" data-model-name="OLED83W69LA" data-model-overallscore="0" data-model-reviewcnt="0" data-model-salesmodelcode="OLED83W69LA.ARUG" data-model-suffixcode="ARUG" data-model-year="2026" data-msrp="0.0" data-price=".00" data-sc-item="where-to-buy" data-sku="OLED83W69LA" data-sub-category-name="QNED_TV" data-super-category-name="tv-audio-video" href="/ru/televisions/lg-oled83w69la#pdp_where">Где купить</a>
</div>
<input name="pdrCompareUseFlag" type="hidden" value="Y"/>
<div class="wishlist-compare">
<a class="link-text ico-compare js-compare" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="QNED_TV" data-buname-two="TV" data-category-name="TV" data-link-area="product_list-model_list" data-link-name="add_to_compare" data-model-id="MD07610675" data-model-name="OLED83W69LA" data-model-overallscore="0" data-model-reviewcnt="0" data-model-salesmodelcode="OLED83W69LA.ARUG" data-model-suffixcode="ARUG" data-model-year="2026" data-msrp="0.0" data-price=".00" data-sku="OLED83W69LA" data-sub-category-name="QNED_TV" data-super-category-name="tv-audio-video" href="#" role="button"><span><span class="hidden-xs">Добавить для сравнения</span><span class="visible-xs">сравнить</span></span><span class="add sr-only">Добавить для сравнения</span><span class="remove sr-only">Удалить из сравнения</span></a>
</div>
</div>
</div>
</div><div class="item js-model" itemscope="" itemtype="http://schema.org/Product">
<meta alt="55-дюймовый телевизор Smart TV LG NANO 4K UHD AI NU80 2026" content="/ru/images/televisions/md09003547/md09003547-350x350.jpg" itemprop="image"/>
<div class="sr-only" itemprop="offers" itemscope="" itemtype="http://schema.org/Offer">
<link href="/ru/televisions/lg-55nu800b6la" itemprop="url"/>
<meta alt="55-дюймовый телевизор Smart TV LG NANO 4K UHD AI NU80 2026" content="/ru/images/televisions/md09003547/md09003547-350x350.jpg" itemprop="image"/>
<meta content="RUB" itemprop="priceCurrency"/>
<meta content="https://schema.org/NewCondition" itemprop="itemCondition"/>
<meta content="0" itemprop="price"/>
</div>
<div class="tag-content">
<p class="d-none" data-user-type="ALL"><span>Новинка</span></p>
</div>
<div class="pd-sideInfo" data-wish-model-id="MD09003547" data-wish-url="/ru/mkt/ajax/product/retrieveWishListCntAndWishOn">
<div class="pd-share share-common">
<button class="sns" type="button"><span class="visually-hidden">SNS Share</span></button>
<div class="list">
<div class="sns-inner">
<strong>Поделиться этой информацией. Вы можете рассказать о понравившемся товаре своим друзьям.</strong>
<ul class="sns-share" data-get-url="https://www.lg.com/common/shorturl/getShortUrl.lgajax" data-param-name="longUrl">
<li><a class="share-vk" data-adobe-copy-icon="vk" data-adobe-tracking-wish="Y" data-image="https://www.lg.com/ru/images/televisions/md09003547/md09003547-350x350.jpg" data-link-area="social_share-share_layer_popup" data-link-name="vk" data-page-event="plp_share_copyicon" data-title="Телевизоры LG - купить ТВ ЛДЖИ по выгодной цене, все модели, отзывы | LG Russia" data-url="https://www.lg.com/ru/televisions/lg-55nu800b6la" href="#" title="VK, Открыть новое окно">VK</a></li>
<li><a class="share-ok" data-adobe-copy-icon="ok" data-adobe-tracking-wish="Y" data-image="https://www.lg.com/ru/images/televisions/md09003547/md09003547-350x350.jpg" data-link-area="social_share-share_layer_popup" data-link-name="ok" data-page-event="plp_share_copyicon" data-title="Телевизоры LG - купить ТВ ЛДЖИ по выгодной цене, все модели, отзывы | LG Russia" data-url="https://www.lg.com/ru/televisions/lg-55nu800b6la" href="#" title="OK, Открыть новое окно">OK</a></li>
<li><a class="article-link" data-adobe-copy-icon="urlcopy" data-adobe-tracking-wish="Y" data-copy-url="https://www.lg.com/ru/televisions/lg-55nu800b6la" data-page-event="plp_share_copyicon" data-toggle="modal" href="#share-complete">Copy Url</a></li>
</ul>
<div class="plp-snsClose">
<button class="btn btn-primary btn-sm" type="button">Закрыть</button>
</div>
</div>
</div>
</div>
</div>
<a aria-hidden="true" class="visual" data-adobe-modelname="55NU800B6LA" data-adobe-salesmodelcode="55NU800B6LA" data-adobe-salessuffixcode="ARUQ" data-link-area="product_list-model_list" data-link-name="55nu800b6la" href="/ru/televisions/lg-55nu800b6la" tabindex="-1">
<img alt="Вид спереди на телевизор LG NANO 4K UHD AI NU80, выпущенный в 2026 году, экран которого заполнен богато текстурированными слоями цвета, напоминающими ткань, где яркие разноцветные складки плавно переп1" class="pc js-thumbnail-loop lazyload" data-img-list="/ru/images/televisions/md09003547/md09003547-350x350.jpg,/ru/images/televisions/md09003547/thumbnail/350-m02.jpg,/ru/images/televisions/md09003547/thumbnail/medium02.jpg,/ru/images/televisions/md09003547/thumbnail/medium03.jpg,/ru/images/televisions/md09003547/thumbnail/medium04.jpg,/ru/images/televisions/md09003547/thumbnail/medium05.jpg" data-src="/ru/images/televisions/md09003547/md09003547-350x350.jpg" itemprop="image"/>
<img alt="Вид спереди на телевизор LG NANO 4K UHD AI NU80, выпущенный в 2026 году, экран которого заполнен богато текстурированными слоями цвета, напоминающими ткань, где яркие разноцветные складки плавно переп2" class="lazyload mobile" data-src="/ru/images/televisions/md09003547/md09003547-260x260.jpg" itemprop="image"/>
</a>
<div class="model-group">
</div>
<div class="products-info" data-model-id="MD09003547"> <div class="pd-group2">
<p class="model-name" itemprop="name"><a data-adobe-tracking-wish="Y" data-link-area="product_list-model_list" data-link-name="55nu800b6la" data-page-event="plp_modelname" href="/ru/televisions/lg-55nu800b6la">55-дюймовый телевизор Smart TV LG NANO 4K UHD AI NU80 2026</a></p>
<div class="sku" data-model-name="55NU800B6LA">
<a data-adobe-tracking-wish="Y" data-link-area="product_list-model_list" data-link-name="55nu800b6la" data-page-event="plp_modelname" href="/ru/televisions/lg-55nu800b6la">
										55NU800B6LA
										</a>
<a class="copy-model-name" href="#" title="Скопировать название модели">Скопировать название модели</a>
</div>
</div>
<div class="rating rating-ru-box" data-adobe-tracking-wish="Y" data-page-event="plp_star_link_click"><a href="/ru/televisions/lg-55nu800b6la#pdp_review"><span data-shoppilot="55nu800b6la"></span></a></div>
<div class="label-list">
<div class="label-inner">
<ul>
<li>
<p>Наноусилитель деталей (Nano Detail Enhancer) улучшает текстуру и глубину для изображения в 4K</p>
</li>
<li>
<p>Платформа webOS предлагает передовые возможности ИИ на базе Google Gemini и Microsoft Copilot</p>
</li>
<li>
<p>ИИ Хаб открывает доступ к интеллектуальному персонализированному использованию, защищенному LG Shield</p>
</li>
</ul>
</div>
<button type="button"><span class="visually-hidden">Open</span></button>
</div>
<div class="promotion-tag-text promotionTagText box-type d-none" data-promotion-tag-text-flag="N" data-promotion-tag-text-pdp-link="promotion_tag_text_pdp_link" data-promotion-tag-text-pdp-link-id-login="promotion_tag_text_pdp_link_in_login">
<a class="promotion-tag-text-link" href="#"><p class="info-txt d-none"><span>Extra coupon alleen voor leden</span></p></a>
</div>
<div class="model-button">
<div class="button">
<a class="btn btn-outline-secondary btn-sm add-to-cart" data-link-area="product_list-model_list" data-link-name="add_to_cart" data-model-id="MD09003547" href="#" role="button">добавить в корзину</a>
<a class="btn btn btn-primary btn-sm where-to-buy active" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="NanoCell_TV" data-buname-two="TV" data-category-name="TV" data-content-id="MD09003547" data-link-area="product_list-model_list" data-link-name="where_to_buy" data-model-id="MD09003547" data-model-name="55NU800B6LA" data-model-overallscore="0" data-model-reviewcnt="0" data-model-salesmodelcode="55NU800B6LA.ARUQ" data-model-suffixcode="ARUQ" data-model-year="2026" data-msrp="0.0" data-price=".00" data-sc-item="where-to-buy" data-sku="55NU800B6LA" data-sub-category-name="NanoCell_TV" data-super-category-name="tv-audio-video" href="/ru/televisions/lg-55nu800b6la#pdp_where">Где купить</a>
</div>
<input name="pdrCompareUseFlag" type="hidden" value="Y"/>
<div class="wishlist-compare">
<a class="link-text ico-compare js-compare" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="NanoCell_TV" data-buname-two="TV" data-category-name="TV" data-link-area="product_list-model_list" data-link-name="add_to_compare" data-model-id="MD09003547" data-model-name="55NU800B6LA" data-model-overallscore="0" data-model-reviewcnt="0" data-model-salesmodelcode="55NU800B6LA.ARUQ" data-model-suffixcode="ARUQ" data-model-year="2026" data-msrp="0.0" data-price=".00" data-sku="55NU800B6LA" data-sub-category-name="NanoCell_TV" data-super-category-name="tv-audio-video" href="#" role="button"><span><span class="hidden-xs">Добавить для сравнения</span><span class="visible-xs">сравнить</span></span><span class="add sr-only">Добавить для сравнения</span><span class="remove sr-only">Удалить из сравнения</span></a>
</div>
</div>
</div>
</div><div class="item js-model">
<div class="tag-content">
<p class="d-none" data-user-type="*productTag1UserType*"><span data-key="productTag1">*productTag1*</span></p>
<p class="d-none" data-user-type="*productTag2UserType*"><span data-key="productTag2">*productTag2*</span></p>
</div>
<div class="pd-sideInfo" data-wish-model-id="*modelId*" data-wish-url="/ru/mkt/ajax/product/retrieveWishListCntAndWishOn">
<div class="pd-share share-common">
<button class="sns" type="button"><span class="visually-hidden">SNS Share</span></button>
<div class="list">
<div class="sns-inner">
<strong>Поделиться этой информацией. Вы можете рассказать о понравившемся товаре своим друзьям.</strong>
<ul class="sns-share" data-get-url="https://www.lg.com/common/shorturl/getShortUrl.lgajax" data-param-name="longUrl">
<li><a class="share-vk" data-adobe-copy-icon="vk" data-adobe-tracking-wish="Y" data-image="*mediumImageAddr*" data-link-area="social_share-share_layer_popup" data-link-name="facebook" data-page-event="plp_share_copyicon" data-title="Телевизоры LG - купить ТВ ЛДЖИ по выгодной цене, все модели, отзывы | LG Russia" data-url="https://www.lg.com*modelUrlPath*" href="#" title="VK, Открыть новое окно">VK</a></li>
<li><a class="share-ok" data-adobe-copy-icon="ok" data-adobe-tracking-wish="Y" data-image="*mediumImageAddr*" data-link-area="social_share-share_layer_popup" data-link-name="facebook" data-page-event="plp_share_copyicon" data-title="Телевизоры LG - купить ТВ ЛДЖИ по выгодной цене, все модели, отзывы | LG Russia" data-url="https://www.lg.com*modelUrlPath*" href="#" title="OK, Открыть новое окно">OK</a></li>
<li><a class="article-link" data-adobe-copy-icon="urlcopy" data-adobe-tracking-wish="Y" data-copy-url="https://www.lg.com*modelCopyUrl*" data-link-name="facebook" data-page-event="plp_share_copyicon" data-toggle="modal" href="#share-complete">Copy Url</a></li>
</ul>
<div class="plp-snsClose">
<button class="btn btn-primary btn-sm" type="button">Закрыть</button>
</div>
</div>
</div>
</div>
</div>
<a aria-hidden="true" class="visual" data-adobe-modelname="*modelName*" data-adobe-salesmodelcode="*salesModelCode*" data-adobe-salessuffixcode="*salesSuffixCode*" data-link-area="product_list-model_list" data-link-name="*modelName*" href="*modelUrlPath*" rel="nofollow" tabindex="-1">
<img alt="*imageAltText*" class="lazyload pc" data-src="*mediumImageAddr*" itemprop="image"/>
<img alt="*imageAltText*" class="lazyload mobile" data-src="*smallImageAddr*" itemprop="image"/>
</a>
<div class="model-group">
<div class="inner">
<template aria-label="Размер экрана" class="size">
<a aria-checked="false" class="size active" data-href="*subModelId*" data-link-area="product_list-model_list-sibling" data-link-name="*siblingType*" href="#" role="radio">*siblingValue*</a>
</template>
<template aria-label="Цвет" class="color">
<a aria-checked="false" class="swatch *siblingCode* active" data-href="*subModelId*" data-link-area="product_list-model_list-sibling" data-link-name="*siblingType*" href="#" role="radio" title="*userFriendlyName* - *siblingValue*"><span class="sr-only">*siblingValue*</span></a>
</template>
</div>
</div>
<div class="products-info" data-model-id="*modelId*"> <div class="pd-group2">
<p class="model-name"><a data-adobe-tracking-wish="Y" data-link-area="product_list-model_list" data-link-name="*modelName*" data-page-event="plp_modelname" href="*modelUrlPath*" rel="nofollow">*userFriendlyName*</a></p>
<div class="sku" data-model-name="*modelName*">
<a data-adobe-tracking-wish="Y" data-link-area="product_list-model_list" data-link-name="*modelName*" data-page-event="plp_modelname" href="*modelUrlPath*" rel="nofollow">
										*modelName*
										</a>
<a class="copy-model-name" href="#" title="Скопировать название модели">Скопировать название модели</a>
</div>
</div>
<div class="rating rating-ru-box" data-adobe-tracking-wish="Y" data-page-event="plp_star_link_click"><a href="*modelUrlPath*#pdp_review" rel="nofollow"><span data-shoppilot="*modelName_toLowerCase*"></span></a></div>
									*epsPictogramArea*
									<!-- LGEFR-640 start -->
<!-- LGEFR-640 end -->
<div class="label-list">
<div class="label-inner">
<ul>
<template data-label-type="AWARD">
															*shortDesc*
														</template>
<template data-label-type="FEATURE">
															*shortDesc*
														</template>
<template data-label-type="DELIVERY">
<li class="label-delivery" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon">
<p class="*obsInstallmentCashback1Dnone*">*obsInstallmentCashback1*</p>
<img alt="" aria-hidden="true" class="*obsInstallmentCashback1Dnone2*" src="/lg5-common-gp/images/common/icons/installation.svg"/>
<p class="*obsInstallmentCashback1Dnone2*">component-installMessage</p>
</li>
</template>
<template data-label-type="DELIVERYPERIOD">
<li class="label-delivery" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon">
<img alt="" aria-hidden="true" src="/lg5-common-gp/images/common/icons/installation.svg"/>
<p>component-obsLeadTime1*obsLeadTimeMin*component-obsLeadTime2*obsLeadTimeMax*component-obsLeadTime3</p>
</li>
</template>
<template data-label-type="WARRANTY">
<li class="label-delivery" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon">
<p class="*obsInstallmentCashback2Dnone*">*obsInstallmentCashback2*</p>
<!-- LGEIN-967 Start -->
<img alt="" aria-hidden="true" src="/lg5-common-gp/images/common/icons/warranty.svg"/>
<p class="*obsInstallmentCashback2Dnone2*">component-warrantyMessage</p>
<!-- LGEIN-967 End -->
</li>
</template>
<template data-label-type="BULLET">
<li data-adobe-tracking-wish="Y" data-page-event="plp_labelicon"><p>*shortDesc*</p></li>
</template>
<template data-label-type="SHIPPINGFLAG">
<li class="label-shipping" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon"><img alt="*shortDesc*" aria-hidden="true" src="/lg5-common-gp/images/common/icons/free-shipping_de.svg"/><p>*shortDesc*</p></li>
</template>
<template data-label-type="DELIVERYFLAG">
<li class="label-delivery" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon"><img alt="*shortDesc*" aria-hidden="true" src="/lg5-common-gp/images/common/icons/delievery_de.svg"/><p>*shortDesc*</p></li>
</template>
<template data-label-type="INSTALLATIONFLAG">
<li class="label-warranty" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon"><img alt="*shortDesc*" aria-hidden="true" src="/lg5-common-gp/images/common/icons/installation_de.svg"/><p>*shortDesc*</p></li>
</template>
<template data-label-type="KEYFEATUREFLAG">
<li class="*cssFont*" data-adobe-tracking-wish="Y" data-page-event="plp_labelicon"><p>*shortDesc*</p></li>
</template>
</ul>
</div>
<button type="button"><span class="visually-hidden">Open</span></button>
</div>
<div class="promotion-tag-text promotionTagText box-type *promotionTagTextView*" data-promotion-tag-text-flag="*promotionTagTextFlag*" data-promotion-tag-text-pdp-link="promotion_tag_text_pdp_link" data-promotion-tag-text-pdp-link-id-login="promotion_tag_text_pdp_link_in_login">
<a class="promotion-tag-text-link" href="#"><p class="info-txt *promotionTagTextView*"><span>*promotionTagText*</span></p></a>
</div>
<div class="model-button">
<div class="button">
<a class="btn btn-outline-secondary btn-sm add-to-cart active" data-biztype="*bizType*" data-bu="*buName1*" data-buname-one="*buName1*" data-buname-three="*buName3*" data-buname-two="*buName2*" data-category-name="*buName2*" data-link-area="product_list-model_list" data-link-name="add_to_cart" data-model-id="*modelId*" data-model-name="*modelName*" data-model-overallscore="*reviewRatingStar2*" data-model-reviewcnt="*reviewRating*" data-model-salesmodelcode="*salesModelCode*.*salesSuffixCode*" data-model-suffixcode="*salesSuffixCode*" data-model-year="*modelYear*" data-msrp="*msrp*" data-price="*priceValue*" data-sku="*modelName*" data-sub-category-name="*buName3*" data-super-category-name="*superCategoryName*" href="#none">добавить в корзину</a>
<a class="btn btn btn-primary btn-sm *wtbClass* active" data-biztype="*bizType*" data-bu="*buName1*" data-buname-one="*buName1*" data-buname-three="*buName3*" data-buname-two="*buName2*" data-category-name="*buName2*" data-content-id="*modelId*" data-link-area="product_list-model_list" data-link-name="where_to_buy" data-model-id="*modelId*" data-model-name="*modelName*" data-model-overallscore="*reviewRatingStar2*" data-model-reviewcnt="*reviewRating*" data-model-salesmodelcode="*salesModelCode*.*salesSuffixCode*" data-model-suffixcode="*salesSuffixCode*" data-model-year="*modelYear*" data-msrp="*msrp*" data-price="*priceValue*" data-sc-item="where-to-buy" data-sku="*modelName*" data-sub-category-name="*buName3*" data-super-category-name="*superCategoryName*" href="*whereToBuyUrl*" rel="nofollow">Где купить</a>
<a class="btn btn-primary buyNowUnionBtn active" data-biztype="*bizType*" data-bu="*buName1*" data-buname-one="*buName1*" data-buname-three="*buName3*" data-buname-two="*buName2*" data-category-name="*buName2*" data-compno="0007" data-content-id="*modelId*" data-link-area="product_list-model_list" data-link-name="buy_now_union_store" data-modal-text="component-unionStoreText" data-model-id="*modelId*" data-model-name="*modelName*" data-model-overallscore="*reviewRatingStar2*" data-model-reviewcnt="*reviewRating*" data-model-salesmodelcode="*salesModelCode*.*salesSuffixCode*" data-model-suffixcode="*salesSuffixCode*" data-model-year="*modelYear*" data-msrp="*msrp*" data-price="*priceValue*" data-sc-item="buy-now-union-store" data-sku="*modelName*" data-sub-category-name="*buName3*" data-super-category-name="*superCategoryName*" data-url="*obsPartnerUrl*" href="#none" title="Открывается в новом окне">component-buyNowUnionStore</a>
</div>
<div class="wishlist-compare">
<a class="link-text ico-compare js-compare" data-biztype="*bizType*" data-bu="*buName1*" data-buname-one="*buName1*" data-buname-three="*buName3*" data-buname-two="*buName2*" data-category-name="*buName2*" data-link-area="product_list-model_list" data-link-name="add_to_compare" data-model-id="*modelId*" data-model-name="*modelName*" data-model-overallscore="*reviewRatingStar2*" data-model-reviewcnt="*reviewRating*" data-model-salesmodelcode="*salesModelCode*.*salesSuffixCode*" data-model-suffixcode="*salesSuffixCode*" data-model-year="*modelYear*" data-msrp="*msrp*" data-price="*priceValue*" data-sku="*modelName*" data-sub-category-name="*buName3*" data-super-category-name="*superCategoryName*" href="#" role="button"><span><span class="hidden-xs">Добавить для сравнения</span><span class="visible-xs">сравнить</span></span><span class="add sr-only">Добавить для сравнения</span><span class="remove sr-only">Удалить из сравнения</span></a>
</div>
</div>
</div>
</div></div></div><div class="products-list-group"><div class="list-contents-wrap"><div class="items"><div class="item js-model" itemscope="" itemtype="http://schema.org/Product">
<div class="sr-only" itemprop="offers" itemscope="" itemtype="http://schema.org/Offer">
<link href="/ru/televisions/lg-oled55c4rla" itemprop="url"/>
<meta alt="Телевизор Smart TV OLED evo AI C4 4K 55'' OLED55C4" content="/ru/images/televisions/md07593380/md07593380-350x350.jpg" itemprop="image"/>
<meta content="RUB" itemprop="priceCurrency"/>
<meta content="https://schema.org/NewCondition" itemprop="itemCondition"/>
<meta content="0" itemprop="price"/>
</div>
<input name="bestActionAreaModelId" type="hidden" value="MD07593380"/>
<div class="tag-content">
</div>
<a aria-hidden="true" class="visual" data-adobe-modelname="OLED55C4RLA" data-adobe-salesmodelcode="OLED55C4RLA" data-adobe-salessuffixcode="ARUG" data-link-area="selective_offering-product_list" data-link-name="oled55c4rla" href="/ru/televisions/lg-oled55c4rla" tabindex="-1" 와="">
<img alt="Вид спереди на телевизор LG OLED evo, OLED C4, logo эмблемы «OLED №1 в мире в течение 11 лет» и logo программы webOS Re:New Program на экране, а также звуковую панель снизу" class="lazyload pc" data-src="/ru/images/televisions/md07593380/md07593380-350x350.jpg" itemprop="image"/>
<img alt="Вид спереди на телевизор LG OLED evo, OLED C4, logo эмблемы «OLED №1 в мире в течение 11 лет» и logo программы webOS Re:New Program на экране, а также звуковую панель снизу" class="lazyload mobile" data-src="/ru/images/televisions/md07593380/md07593380-260x260.jpg" itemprop="image"/>
</a>
<div class="model-group">
<div aria-label="Размер экрана" class="inner" role="radiogroup">
<a aria-checked="false" class="size" data-href="MD07593369" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	83"
																</a>
<a aria-checked="false" class="size" data-href="MD07593378" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	77"
																</a>
<a aria-checked="false" class="size" data-href="MD07592966" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	65"
																</a>
<a aria-checked="true" class="size active" data-href="MD07593380" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	55"
																</a>
<a aria-checked="false" class="size" data-href="MD07593382" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	48"
																</a>
<a aria-checked="false" class="size" data-href="MD07593385" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	42"
																</a>
</div>
</div>
<div class="products-info" data-adobe-bu="MS" data-adobe-category="televisions" data-adobe-modelname="OLED55C4RLA" data-adobe-salesmodelcode="OLED55C4RLA" data-adobe-salessuffixcode="ARUG" data-adobe-super-category="tv-audio-video" data-model-id="MD07593380" data-wish-basic-info="">
<p class="model-name" itemprop="name">
<a data-link-area="selective_offering-product_list" data-link-name="oled55c4rla" href="/ru/televisions/lg-oled55c4rla">Телевизор Smart TV OLED evo AI C4 4K 55'' OLED55C4</a>
</p>
<div class="sku">
<a data-adobe-tracking-wish="Y" data-page-event="plp_modelname" href="/ru/televisions/lg-oled55c4rla">
													OLED55C4RLA
													</a>
</div>
<div class="rating rating-ru-box"><a data-link-area="selective_offering-product_list" data-link-name="oled55c4rla" href="/ru/televisions/lg-oled55c4rla#pdp_review"><span data-shoppilot="oled55c4rla"></span></a></div>
<div class="model-buy">
<div class="price-vip-Installment d-none">
<p class="price-vip">
</p>
<a class="price-installment" data-adobe-tracking-wish="Y" data-emi-popup-url="" data-page-event="plp_Installment_Info_click" href="#" title="Открывается в новом окне">
</a>
</div>
<div class="price-area total d-none type-none" data-model-id="MD07593380">
<div class="msrp"></div>
</div>
<div class="member-text d-none">
</div>
<div class="coupon-price d-none" data-sibling-welcomeprice-template="&lt;p&gt;
( 
*siblingObsWelcomePriceDescription*
&lt;em&gt;&lt;span&gt;
*siblingObsWelcomePrice* руб.
&lt;/span&gt;&lt;/em&gt;
 )
&lt;/p&gt;
">
</div>
</div>
<p class="promotion-text">
</p>
<input id="obsBuynowFlag" name="obsBuynowFlag" type="hidden" value="N"/>
<input id="buynow" name="buynow" type="hidden" value="component-buynow"/>
<div class="button">
<a class="btn btn-outline-secondary btn-sm add-to-cart" data-link-area="selective_offering-product_list" data-link-name="add_to_cart" data-model-id="MD07593380" href="#none" role="button">добавить в корзину</a>
<a class="btn btn btn-primary btn-sm where-to-buy active" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="OLED_evo" data-buname-two="TV" data-category-name="TV" data-link-area="selective_offering-product_list" data-link-name="where_to_buy" data-model-id="MD07593380" data-model-name="OLED55C4RLA" data-model-overallscore="0.0" data-model-reviewcnt="0" data-model-salesmodelcode="OLED55C4RLA.ARUG" data-model-suffixcode="ARUG" data-model-year="2024" data-msrp="0.0" data-price=".00" data-sc-item="where-to-buy" data-sku="OLED55C4RLA" data-sub-category-name="OLED_evo" data-super-category-name="tv-audio-video" href="/ru/televisions/lg-oled55c4rla#pdp_where">Где купить</a>
</div>
<div class="wishlist-compare">
<a class="link-text ico-compare js-compare" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="OLED_evo" data-buname-two="TV" data-category-name="TV" data-link-area="selective_offering-product_list" data-link-name="add_to_compare" data-model-id="MD07593380" data-model-name="OLED55C4RLA" data-model-overallscore="0.0" data-model-reviewcnt="0" data-model-salesmodelcode="OLED55C4RLA.ARUG" data-model-suffixcode="ARUG" data-model-year="2024" data-msrp="0.0" data-price=".00" data-sku="OLED55C4RLA" data-sub-category-name="OLED_evo" data-super-category-name="tv-audio-video" href="/ru/mkt/ajax/nbaa/retrieveManualProductList" role="button">
<span><span class="hidden-xs">Добавить для сравнения</span><span class="visible-xs"> сравнить</span></span>
<span class="add sr-only">Добавить для сравнения</span>
<span class="remove sr-only">Удалить из сравнения</span>
</a>
</div>
</div>
</div><div class="item js-model" itemscope="" itemtype="http://schema.org/Product">
<div class="sr-only" itemprop="offers" itemscope="" itemtype="http://schema.org/Offer">
<link href="/ru/televisions/lg-oled55g6rla" itemprop="url"/>
<meta alt="55-дюймовый телевизор Смарт ТВ LG OLED evo AI G6 4K 2026" content="/ru/images/televisions/md08801772/md08801772-350x350.jpg" itemprop="image"/>
<meta content="RUB" itemprop="priceCurrency"/>
<meta content="https://schema.org/NewCondition" itemprop="itemCondition"/>
<meta content="0" itemprop="price"/>
</div>
<input name="bestActionAreaModelId" type="hidden" value="MD08801772"/>
<div class="tag-content">
<span class="d-none" data-user-type="ALL">Новинка</span>
</div>
<a aria-hidden="true" class="visual" data-adobe-modelname="OLED55G6RLA" data-adobe-salesmodelcode="OLED55G6RLA" data-adobe-salessuffixcode="ARUG" data-link-area="selective_offering-product_list" data-link-name="oled55g6rla" href="/ru/televisions/lg-oled55g6rla" tabindex="-1" 와="">
<img alt="Фронтальный вид на телевизор LG OLED evo AI G6, выпущенный в 2026 году, отличается тонкой черной рамкой и ярким скульптурным изображением насыщенных многослойных разноцветных форм, заполняющих экран." class="lazyload pc" data-src="/ru/images/televisions/md08801772/md08801772-350x350.jpg" itemprop="image"/>
<img alt="Фронтальный вид на телевизор LG OLED evo AI G6, выпущенный в 2026 году, отличается тонкой черной рамкой и ярким скульптурным изображением насыщенных многослойных разноцветных форм, заполняющих экран." class="lazyload mobile" data-src="/ru/images/televisions/md08801772/md08801772-260x260.jpg" itemprop="image"/>
</a>
<div class="model-group">
<div aria-label="Размер экрана" class="inner" role="radiogroup">
<a aria-checked="true" class="size active" data-href="MD08801772" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	55"
																</a>
<a aria-checked="false" class="size" data-href="MD07610971" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	65"
																</a>
<a aria-checked="false" class="size" data-href="MD08801809" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	77"
																</a>
<a aria-checked="false" class="size" data-href="MD08801200" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	83"
																</a>
<a aria-checked="false" class="size" data-href="MD08801205" data-link-area="product_list-model_list-sibling" data-link-name="SIZE" href="#" role="radio">
																	97"
																</a>
</div>
</div>
<div class="products-info" data-adobe-bu="MS" data-adobe-category="televisions" data-adobe-modelname="OLED55G6RLA" data-adobe-salesmodelcode="OLED55G6RLA" data-adobe-salessuffixcode="ARUG" data-adobe-super-category="tv-audio-video" data-model-id="MD08801772" data-wish-basic-info="">
<p class="model-name" itemprop="name">
<a data-link-area="selective_offering-product_list" data-link-name="oled55g6rla" href="/ru/televisions/lg-oled55g6rla">55-дюймовый телевизор Смарт ТВ LG OLED evo AI G6 4K 2026</a>
</p>
<div class="sku">
<a data-adobe-tracking-wish="Y" data-page-event="plp_modelname" href="/ru/televisions/lg-oled55g6rla">
													OLED55G6RLA
													</a>
</div>
<div class="rating rating-ru-box"><a data-link-area="selective_offering-product_list" data-link-name="oled55g6rla" href="/ru/televisions/lg-oled55g6rla#pdp_review"><span data-shoppilot="oled55g6rla"></span></a></div>
<div class="model-buy">
<div class="price-vip-Installment d-none">
<p class="price-vip">
</p>
<a class="price-installment" data-adobe-tracking-wish="Y" data-emi-popup-url="" data-page-event="plp_Installment_Info_click" href="#" title="Открывается в новом окне">
</a>
</div>
<div class="price-area total d-none type-none" data-model-id="MD08801772">
<div class="msrp"></div>
</div>
<div class="member-text d-none">
</div>
<div class="coupon-price d-none" data-sibling-welcomeprice-template="&lt;p&gt;
( 
*siblingObsWelcomePriceDescription*
&lt;em&gt;&lt;span&gt;
*siblingObsWelcomePrice* руб.
&lt;/span&gt;&lt;/em&gt;
 )
&lt;/p&gt;
">
</div>
</div>
<p class="promotion-text">
</p>
<input id="obsBuynowFlag" name="obsBuynowFlag" type="hidden" value="N"/>
<input id="buynow" name="buynow" type="hidden" value="component-buynow"/>
<div class="button">
<a class="btn btn-outline-secondary btn-sm add-to-cart" data-link-area="selective_offering-product_list" data-link-name="add_to_cart" data-model-id="MD08801772" href="#none" role="button">добавить в корзину</a>
<a class="btn btn btn-primary btn-sm where-to-buy active" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="OLED_TV" data-buname-two="TV" data-category-name="TV" data-link-area="selective_offering-product_list" data-link-name="where_to_buy" data-model-id="MD08801772" data-model-name="OLED55G6RLA" data-model-overallscore="0.0" data-model-reviewcnt="0" data-model-salesmodelcode="OLED55G6RLA.ARUG" data-model-suffixcode="ARUG" data-model-year="2026" data-msrp="0.0" data-price=".00" data-sc-item="where-to-buy" data-sku="OLED55G6RLA" data-sub-category-name="OLED_TV" data-super-category-name="tv-audio-video" href="/ru/televisions/lg-oled55g6rla#pdp_where">Где купить</a>
</div>
<div class="wishlist-compare">
<a class="link-text ico-compare js-compare" data-biztype="B2C" data-bu="MS" data-buname-one="MS" data-buname-three="OLED_TV" data-buname-two="TV" data-category-name="TV" data-link-area="selective_offering-product_list" data-link-name="add_to_compare" data-model-id="MD08801772" data-model-name="OLED55G6RLA" data-model-overallscore="0.0" data-model-reviewcnt="0" data-model-salesmodelcode="OLED55G6RLA.ARUG" data-model-suffixcode="ARUG" data-model-year="2026" data-msrp="0.0" data-price=".00" data-sku="OLED55G6RLA" data-sub-category-name="OLED_TV" data-super-category-name="tv-audio-video" href="/ru/mkt/ajax/nbaa/retrieveManualProductList" role="button">
<span><span class="hidden-xs">Добавить для сравнения</span><span class="visible-xs"> сравнить</span></span>
<span class="add sr-only">Добавить для сравнения</span>
<span class="remove sr-only">Удалить из сравнения</span>
</a>
</div>
</div>
</div></div></div></div></body></html>"""

API_FIXTURE_JSON = r"""{"data": [{"pageInfo": {"view": "Y", "pageCount": 5, "loopStart": 1, "page": 1, "loopEnd": 5, "totalCount": 50, "rightPage": false, "leftPage": false, "categoryInfo": ""}, "totalCount": 203, "productList": [{"modelId": "MD07610675", "modelName": "OLED83W69LA", "inchCode": "83", "modelStatusCode": "ACTIVE", "msrp": 0.0, "promotionPrice": 0.0, "obsProductUrl": null, "obsOriginalPrice": 0.0, "obsSellingPrice": 0.0, "obsCurrency": null, "obsInventoryFlag": null, "obsProductCount": 0, "obsSellFlag": null, "resellerBtnFlag": "N", "resellerLinkUrl": "", "discountedRate": null, "rDiscountedPrice": null, "rDiscountedPriceCent": null, "rPrice": null, "rPriceCent": null, "rPromoPrice": null, "rPromoPriceCent": null, "addToCartFlag": "N", "findTheDealerFlag": "N", "whereToBuyFlag": "Y", "wtbExternalLinkUseFlag": "N", "wtbExternalLinkName": "", "wtbExternalLinkUrl": "", "wtbExternalLinkSelfFlag": "Y", "inquiryToBuyFlag": "N", "productSupportFlag": "N", "buyNowFlag": "N", "categoryId": "CT20206007", "modelUrlPath": "/ru/televisions/lg-oled83w69la", "categoryName": "Телевизоры", "reviewRating": "0", "reviewRatingStar": "0", "reviewRatingStar2": "0.0", "reviewRatingPercent": "0", "reStockAlertFlag": "N", "reStockAlertUrl": "", "modelRollingImgList": "/ru/images/televisions/md07610675/md07610675-350x350.jpg,/ru/images/televisions/md07610675/thumbnail/350-1.jpg,/ru/images/televisions/md07610675/thumbnail/350-2.jpg,/ru/images/televisions/md07610675/thumbnail/350-3.jpg,/ru/images/televisions/md07610675/thumbnail/350-4.jpg,/ru/images/televisions/md07610675/thumbnail/350-5.jpg", "smallModelRollingImgList": "/ru/images/televisions/md07610675/md07610675-260x260.jpg,/ru/images/televisions/md07610675/thumbnail/260-1.jpg,/ru/images/televisions/md07610675/thumbnail/260-2.jpg,/ru/images/televisions/md07610675/thumbnail/260-3.jpg,/ru/images/televisions/md07610675/thumbnail/260-4.jpg,/ru/images/televisions/md07610675/thumbnail/260-5.jpg", "sortBy": null, "siblingGroupCode": "W69LA_RU", "siblingCode": "83", "defaultSiblingModelFlag": "Y", "plpHighlightModelFlag": "Y", "siblingLocalValue": "83\"", "target": "NEW", "siblingType": "SIZE", "totalCount": 203, "promotionTotalCount": 0, "totalSize": 50, "bizType": "B2C", "wtbUseFlag": "Y", "userFriendlyName": "83-дюймовый телевизор Smart TV Wallpaper TV LG OLED evo AI W6 4K 2026 года", "mediumImageAddr": "/ru/images/televisions/md07610675/md07610675-350x350.jpg", "smallImageAddr": "/ru/images/televisions/md07610675/md07610675-260x260.jpg", "imageAltText": "Вид спереди на телевизор LG OLED evo AI W6 Wallpaper TV, выпущенный в 2026 году, демонстрирует элегантный дизайн Wallpaper, а динамичная абстрактная композиция волнообразных градиентов ярких цветов пл", "defaultProductTag": "Новинка", "productTag1": "Новинка", "productTag2": "", "productTag1UserType": "ALL", "productTag2UserType": "", "preOrderTagEnableFlag": null, "obsComTagShowFlag": "N", "productTag1Type": "COM", "productTag2Type": "COM", "whereToBuyUrl": "/ru/televisions/lg-oled83w69la#pdp_where", "findTheDealerUrl": null, "inquiryToBuyUrl": null, "retailerPricingFlag": "N", "retailerPricingText": "Смотреть ритейлеров для ценообразования", "siblingModels": [{"modelName": "OLED83W69LA", "siblingCode": "83", "siblingValue": "83\"", "modelId": "MD07610675"}, {"modelName": "OLED77W69LA", "siblingCode": "77", "siblingValue": "77\"", "modelId": "MD08807920"}], "promotionText": null, "modelType": "G", "bundlePlpDisplayFlag": "Y", "obsTotalCount": 0, "bundlesTotalCount": 0, "salesModelCode": "OLED83W69LA", "salesSuffixCode": "ARUG", "energyLabel": null, "energyLabelFileName": null, "energyLabelOriginalName": null, "productFicheFileName": null, "productFicheOriginalName": null, "energyLabelDocId": null, "productFicheDocId": null, "energyLabelImageAddr": null, "energyLabelName": null, "energyLabelCategory": null, "reviewType": null, "productMessages": null, "productSupportUrl": null, "buyNowUrl": "", "discountMsg": null, "ecommerceTarget": "_blank", "releaseYear": null, "releaseDate": null, "obsLoginFlag": "N", "buyNowUseFlag": null, "promotionLinkUrl": null, "externalLinkTarget": null, "obsVipPrice": null, "vipPriceFlag": "N", "obsVipTotalCount": 0, "obsBuynowFlag": "", "labelIconMap": [{"iconId": "", "shortDescType": "BULLET", "altText": "", "linkOpt": "", "modelId": "MD07610675", "linkUrl": "", "shortDesc": "Благодаря толщине всего 9 мм дизайн Wallpaper привносит эстетичность в окружающее пространство", "imagePathAddr": "", "TEMP_SEQ": "8", "cssFontBold": "", "cssFontItalic": "", "repOrderNo": "1"}, {"iconId": "", "shortDescType": "BULLET", "altText": "", "linkOpt": "", "modelId": "MD07610675", "linkUrl": "", "shortDesc": "Технология беспроводной передачи данных 4K 165 Гц для безупречного качества изображения", "imagePathAddr": "", "TEMP_SEQ": "8", "cssFontBold": "", "cssFontItalic": "", "repOrderNo": "2"}, {"iconId": "", "shortDescType": "BULLET", "altText": "", "linkOpt": "", "modelId": "MD07610675", "linkUrl": "", "shortDesc": "Технология сверхсияющего цвета в телевизорах LG OLED нового поколения для нового уровня качества изображения", "imagePathAddr": "", "TEMP_SEQ": "8", "cssFontBold": "", "cssFontItalic": "", "repOrderNo": "3"}], "whiteSpaceMap": [], "signatureFlag": "N", "thinqFlag": "N", "modelBrand": null, "tagContentAreaYn": "Y", "modelBrandAreaYn": "N", "siblingAreaYn": "Y", "reviewAreaYn": "N", "promotionAreaYn": "N", "priceAreaYn": "N", "energyFicheAreaYn": "N", "btnAreaYn": "T", "specMsgFlagAreaYn": "N", "obsLimitSale": "N", "limitSaleUseFlag": "N", "limitSaleTitle": "Продажа ограниченным количеством", "limitSaleAreaYn": "N", "buName1": "MS", "buName2": "TV", "buName3": "QNED_TV", "superCategoryName": "tv-audio-video", "categoryEngName": "televisions", "wishTotalCnt": "0", "myWishCnt": "N", "domain": null, "fEnergyLabelDocId": null, "fEnergyLabelFileName": null, "fEnergyLabelOriginalName": null, "obsPreOrderEnableFlag": null, "obsPreOrderInventoryFlag": null, "obsPreOrderStartDate": null, "obsPreOrderEndDate": null, "obsPreOrderValidDateFlag": "N", "obsPreOrderTotalPreQuantity": null, "obsPreOrderSalablePreQuantity": null, "obsPreOrderFlag": "N", "obsPreOrderRSAFlag": "N", "obsInstallmentFee": null, "obsInstallmentMonth": null, "obsInstallmentPrice": null, "obsInstallmentTan": null, "obsInstallmentTaeg": null, "obsInstallmentTotalPrice": null, "emiMsg": "", "emiMsgAreaYn": "N", "emiPopupUrl": "", "obsEmiMsgFlag": "N", "modelYear": "2026", "specMsgFlag": null, "obsMembershipPrice": 0.0, "rMembershipPrice": null, "rMembershipPriceCent": null, "membershipDisplayFlag": "N", "starRatingValue": null, "participantCount": null, "starRatingPercent": null, "obsCheaperPrice": 0.0, "cheaperPrice": null, "cheaperPriceCent": null, "cheaperPriceFlag": "N", "lowestPriceFlag": null, "obsLowestPriceFlag": "N", "obsLowestPrice": null, "obsLowestPriceCent": null, "obsVipLowestPriceFlag": "N", "obsVipLowestPrice": null, "obsVipLowestPriceCent": null, "afterPay": "0", "obsInstallmentMemberPrice": null, "obsInstallmentMemberMonth": null, "obsInstallmentMemberFee": null, "obsInstallmentMemberTan": null, "obsInstallmentMemberTaeg": null, "obsInstallmentMemberTotalPrice": null, "emiMemberMsg": "", "recommendedRetailRriceInfo": null, "obsMembershipLinkUseFlag": "N", "obsMembershipLinkUrl": "", "obsMembershipLinkTarget": "", "obsPreOrderCount": 0, "obsPartnerUrl": "", "buyNowUnionStoreBtnFlag": "N", "obsZipPayMsg": null, "pdrCompareUseFlag": "Y", "obsLeadTimeFlag": "N", "obsLeadTimeMin": "", "obsLeadTimeMax": "", "promotionTagTextFlag": "N", "promotionTagText": "Extra coupon alleen voor leden", "secondEnergyLabel": null, "secondEnergyLabelFileName": null, "secondEnergyLabelOriginalName": null, "secondProductFicheFileName": null, "secondProductFicheOriginalName": null, "secondEnergyLabelDocId": null, "secondProductFicheDocId": null, "secondEnergyLabelImageAddr": null, "secondEnergyLabelName": null, "secondEnergyLabelCategory": null, "washTowerFlag": "N", "secondFEnergyLabelDocId": null, "secondFEnergyLabelFileName": null, "secondFEnergyLabelOriginalName": null, "energyLabelproductLeve1Code": null, "fenergyLabelproductLeve1Code": null, "productFicheproductLeve1Code": null, "secondEnergyLabelproductLeve1Code": null, "secondFEnergyLabelproductLeve1Code": null, "secondProductFicheproductLeve1Code": null, "firstLabelCheckFlag": null, "obsInstallmentCashback1": null, "obsInstallmentCashback2": null, "obsInstallmentMemberCashback1": "", "obsInstallmentMemberCashback2": "", "userGroup": null, "obsInstallmentInterestFlag": null, "labelRepairMap": [], "repairModelAreaYn": "N", "productTag1ColorFlag": "N", "productTag2ColorFlag": "N", "docTypeCodeFlag": "", "hideInstallationMessageFlag": "N", "obsInsatllationDisplayFlag": null, "obsSubscriptionEnableFlag": null, "obsSubscriptionStatus": null, "obsSubscriptionMaxMonth": null, "obsSubscriptionMonthlyCost": 0.0, "obsSubscriptionDisclaimer": null, "obsSubscriptionLandingPageUrl": null, "obsSubscriptionCtaLinkTarget": "", "obsConvertSubscriptionMonthlyCost": "", "obsConvertSubscriptionMonthlyCostCent": "", "exchanageOfferFlag": null, "guestPriceMessage": null, "guestPriceMessageUseFlag": null, "obsWelcomePriceUseFlag": "N", "obsWelcomePrice": "", "obsWelcomePriceCent": "", "obsWelcomePriceDescription": "", "pisDocType": null, "pisDocOldFlag": null, "secondPisDocType": null, "secondPfCode": null, "elType": null, "secondElType": null, "b2cPriceOnVipGroupsUseFlag": "N", "epsDocType": null, "epsPictoFlag": "N", "includeChargerYn": null, "minPower": null, "maxPower": null, "usbPdYn": null, "energyLabelUpperTextUseFlag": "N", "energyLabelUpperText": null, "localeCode": "RU", "tempdata": 0}, {"modelId": "MD09003547", "modelName": "55NU800B6LA", "inchCode": "55", "modelStatusCode": "ACTIVE", "msrp": 0.0, "promotionPrice": 0.0, "obsProductUrl": null, "obsOriginalPrice": 0.0, "obsSellingPrice": 0.0, "obsCurrency": null, "obsInventoryFlag": null, "obsProductCount": 0, "obsSellFlag": null, "resellerBtnFlag": "N", "resellerLinkUrl": "", "discountedRate": null, "rDiscountedPrice": null, "rDiscountedPriceCent": null, "rPrice": null, "rPriceCent": null, "rPromoPrice": null, "rPromoPriceCent": null, "addToCartFlag": "N", "findTheDealerFlag": "N", "whereToBuyFlag": "Y", "wtbExternalLinkUseFlag": "N", "wtbExternalLinkName": "", "wtbExternalLinkUrl": null, "wtbExternalLinkSelfFlag": "Y", "inquiryToBuyFlag": "N", "productSupportFlag": "N", "buyNowFlag": "N", "categoryId": "CT20206007", "modelUrlPath": "/ru/televisions/lg-55nu800b6la", "categoryName": "Телевизоры", "reviewRating": "0", "reviewRatingStar": "0", "reviewRatingStar2": "0.0", "reviewRatingPercent": "0", "reStockAlertFlag": "N", "reStockAlertUrl": "", "modelRollingImgList": "/ru/images/televisions/md09003547/md09003547-350x350.jpg,/ru/images/televisions/md09003547/thumbnail/350-m02.jpg,/ru/images/televisions/md09003547/thumbnail/medium02.jpg,/ru/images/televisions/md09003547/thumbnail/medium03.jpg,/ru/images/televisions/md09003547/thumbnail/medium04.jpg,/ru/images/televisions/md09003547/thumbnail/medium05.jpg", "smallModelRollingImgList": "/ru/images/televisions/md09003547/md09003547-260x260.jpg,/ru/images/televisions/md09003547/thumbnail/260-m02.jpg,/ru/images/televisions/md09003547/thumbnail/small02.jpg,/ru/images/televisions/md09003547/thumbnail/small03.jpg,/ru/images/televisions/md09003547/thumbnail/small04.jpg,/ru/images/televisions/md09003547/thumbnail/small05.jpg", "sortBy": null, "siblingGroupCode": null, "siblingCode": null, "defaultSiblingModelFlag": null, "plpHighlightModelFlag": "Y", "siblingLocalValue": null, "target": null, "siblingType": null, "totalCount": 203, "promotionTotalCount": 0, "totalSize": 50, "bizType": "B2C", "wtbUseFlag": "Y", "userFriendlyName": "55-дюймовый телевизор Smart TV LG NANO 4K UHD AI NU80 2026", "mediumImageAddr": "/ru/images/televisions/md09003547/md09003547-350x350.jpg", "smallImageAddr": "/ru/images/televisions/md09003547/md09003547-260x260.jpg", "imageAltText": "Вид спереди на телевизор LG NANO 4K UHD AI NU80, выпущенный в 2026 году, экран которого заполнен богато текстурированными слоями цвета, напоминающими ткань, где яркие разноцветные складки плавно переп", "defaultProductTag": "Новинка", "productTag1": "Новинка", "productTag2": "", "productTag1UserType": "ALL", "productTag2UserType": "", "preOrderTagEnableFlag": null, "obsComTagShowFlag": "N", "productTag1Type": "COM", "productTag2Type": "COM", "whereToBuyUrl": "/ru/televisions/lg-55nu800b6la#pdp_where", "findTheDealerUrl": null, "inquiryToBuyUrl": null, "retailerPricingFlag": "N", "retailerPricingText": "Смотреть ритейлеров для ценообразования", "siblingModels": [], "promotionText": null, "modelType": "G", "bundlePlpDisplayFlag": "Y", "obsTotalCount": 0, "bundlesTotalCount": 0, "salesModelCode": "55NU800B6LA", "salesSuffixCode": "ARUQ", "energyLabel": null, "energyLabelFileName": null, "energyLabelOriginalName": null, "productFicheFileName": null, "productFicheOriginalName": null, "energyLabelDocId": null, "productFicheDocId": null, "energyLabelImageAddr": null, "energyLabelName": null, "energyLabelCategory": null, "reviewType": null, "productMessages": null, "productSupportUrl": null, "buyNowUrl": "", "discountMsg": null, "ecommerceTarget": "_blank", "releaseYear": null, "releaseDate": null, "obsLoginFlag": "N", "buyNowUseFlag": null, "promotionLinkUrl": null, "externalLinkTarget": null, "obsVipPrice": null, "vipPriceFlag": "N", "obsVipTotalCount": 0, "obsBuynowFlag": "", "labelIconMap": [{"iconId": "", "shortDescType": "BULLET", "altText": "", "linkOpt": "", "modelId": "MD09003547", "linkUrl": "", "shortDesc": "Наноусилитель деталей (Nano Detail Enhancer) улучшает текстуру и глубину для изображения в 4K", "imagePathAddr": "", "TEMP_SEQ": "8", "cssFontBold": "", "cssFontItalic": "", "repOrderNo": "1"}, {"iconId": "", "shortDescType": "BULLET", "altText": "", "linkOpt": "", "modelId": "MD09003547", "linkUrl": "", "shortDesc": "Платформа webOS предлагает передовые возможности ИИ на базе Google Gemini и Microsoft Copilot", "imagePathAddr": "", "TEMP_SEQ": "8", "cssFontBold": "", "cssFontItalic": "", "repOrderNo": "2"}, {"iconId": "", "shortDescType": "BULLET", "altText": "", "linkOpt": "", "modelId": "MD09003547", "linkUrl": "", "shortDesc": "ИИ Хаб открывает доступ к интеллектуальному персонализированному использованию, защищенному LG Shield", "imagePathAddr": "", "TEMP_SEQ": "8", "cssFontBold": "", "cssFontItalic": "", "repOrderNo": "3"}], "whiteSpaceMap": [], "signatureFlag": "N", "thinqFlag": "N", "modelBrand": null, "tagContentAreaYn": "Y", "modelBrandAreaYn": "N", "siblingAreaYn": "Y", "reviewAreaYn": "N", "promotionAreaYn": "N", "priceAreaYn": "N", "energyFicheAreaYn": "N", "btnAreaYn": "T", "specMsgFlagAreaYn": "N", "obsLimitSale": "N", "limitSaleUseFlag": "N", "limitSaleTitle": "Продажа ограниченным количеством", "limitSaleAreaYn": "N", "buName1": "MS", "buName2": "TV", "buName3": "NanoCell_TV", "superCategoryName": "tv-audio-video", "categoryEngName": "televisions", "wishTotalCnt": "0", "myWishCnt": "N", "domain": null, "fEnergyLabelDocId": null, "fEnergyLabelFileName": null, "fEnergyLabelOriginalName": null, "obsPreOrderEnableFlag": null, "obsPreOrderInventoryFlag": null, "obsPreOrderStartDate": null, "obsPreOrderEndDate": null, "obsPreOrderValidDateFlag": "N", "obsPreOrderTotalPreQuantity": null, "obsPreOrderSalablePreQuantity": null, "obsPreOrderFlag": "N", "obsPreOrderRSAFlag": "N", "obsInstallmentFee": null, "obsInstallmentMonth": null, "obsInstallmentPrice": null, "obsInstallmentTan": null, "obsInstallmentTaeg": null, "obsInstallmentTotalPrice": null, "emiMsg": "", "emiMsgAreaYn": "N", "emiPopupUrl": "", "obsEmiMsgFlag": "N", "modelYear": "2026", "specMsgFlag": null, "obsMembershipPrice": 0.0, "rMembershipPrice": null, "rMembershipPriceCent": null, "membershipDisplayFlag": "N", "starRatingValue": null, "participantCount": null, "starRatingPercent": null, "obsCheaperPrice": 0.0, "cheaperPrice": null, "cheaperPriceCent": null, "cheaperPriceFlag": "N", "lowestPriceFlag": null, "obsLowestPriceFlag": "N", "obsLowestPrice": null, "obsLowestPriceCent": null, "obsVipLowestPriceFlag": "N", "obsVipLowestPrice": null, "obsVipLowestPriceCent": null, "afterPay": "0", "obsInstallmentMemberPrice": null, "obsInstallmentMemberMonth": null, "obsInstallmentMemberFee": null, "obsInstallmentMemberTan": null, "obsInstallmentMemberTaeg": null, "obsInstallmentMemberTotalPrice": null, "emiMemberMsg": "", "recommendedRetailRriceInfo": null, "obsMembershipLinkUseFlag": "N", "obsMembershipLinkUrl": "", "obsMembershipLinkTarget": "", "obsPreOrderCount": 0, "obsPartnerUrl": "", "buyNowUnionStoreBtnFlag": "N", "obsZipPayMsg": null, "pdrCompareUseFlag": "Y", "obsLeadTimeFlag": "N", "obsLeadTimeMin": "", "obsLeadTimeMax": "", "promotionTagTextFlag": "N", "promotionTagText": "Extra coupon alleen voor leden", "secondEnergyLabel": null, "secondEnergyLabelFileName": null, "secondEnergyLabelOriginalName": null, "secondProductFicheFileName": null, "secondProductFicheOriginalName": null, "secondEnergyLabelDocId": null, "secondProductFicheDocId": null, "secondEnergyLabelImageAddr": null, "secondEnergyLabelName": null, "secondEnergyLabelCategory": null, "washTowerFlag": "N", "secondFEnergyLabelDocId": null, "secondFEnergyLabelFileName": null, "secondFEnergyLabelOriginalName": null, "energyLabelproductLeve1Code": null, "fenergyLabelproductLeve1Code": null, "productFicheproductLeve1Code": null, "secondEnergyLabelproductLeve1Code": null, "secondFEnergyLabelproductLeve1Code": null, "secondProductFicheproductLeve1Code": null, "firstLabelCheckFlag": null, "obsInstallmentCashback1": null, "obsInstallmentCashback2": null, "obsInstallmentMemberCashback1": "", "obsInstallmentMemberCashback2": "", "userGroup": null, "obsInstallmentInterestFlag": null, "labelRepairMap": [], "repairModelAreaYn": "N", "productTag1ColorFlag": "N", "productTag2ColorFlag": "N", "docTypeCodeFlag": "", "hideInstallationMessageFlag": "N", "obsInsatllationDisplayFlag": null, "obsSubscriptionEnableFlag": null, "obsSubscriptionStatus": null, "obsSubscriptionMaxMonth": null, "obsSubscriptionMonthlyCost": 0.0, "obsSubscriptionDisclaimer": null, "obsSubscriptionLandingPageUrl": null, "obsSubscriptionCtaLinkTarget": "", "obsConvertSubscriptionMonthlyCost": "", "obsConvertSubscriptionMonthlyCostCent": "", "exchanageOfferFlag": null, "guestPriceMessage": null, "guestPriceMessageUseFlag": null, "obsWelcomePriceUseFlag": "N", "obsWelcomePrice": "", "obsWelcomePriceCent": "", "obsWelcomePriceDescription": "", "pisDocType": null, "pisDocOldFlag": null, "secondPisDocType": null, "secondPfCode": null, "elType": null, "secondElType": null, "b2cPriceOnVipGroupsUseFlag": "N", "epsDocType": null, "epsPictoFlag": "N", "includeChargerYn": null, "minPower": null, "maxPower": null, "usbPdYn": null, "energyLabelUpperTextUseFlag": "N", "energyLabelUpperText": null, "localeCode": "RU", "tempdata": 0}]}], "message": "", "status": "success", "dataCount": 1}"""

SUPPORT_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>E-mail Президенту | LG</title>
<link rel="stylesheet" href="/lg5-common-gp/css/common.css"/></head><body>
<form id="inquiryForm"><captcha-widgets><captcha-widget data-captcha-type="recaptcha"
  data-widget-id="100000" data-version="v3" data-sitekey="6LFIXTUREFIXTUREFIXTUREFIXTURE12"
  data-action="null" data-loaded="true"></captcha-widget></captcha-widgets></form>
</body></html>"""

# A page the site plainly served with the grid not rendered into it yet.
SHELL_FIXTURE_HTML = ("<!DOCTYPE html><html><head><title>LG</title>" +
                      "".join(f'<link rel="preload" href="/lg5-common-gp/js/a{i}.js">'
                              for i in range(20)) +
                      '</head><body><div class="result-box"></div></body></html>')

# Chromium's own network-error page. It carries the SITE'S OWN HOSTNAME in
# its title, so every text marker reads it as a real page — only "was this
# built out of the site's own assets?" gets it right.
CHROME_ERROR_FIXTURE_HTML = ("<!DOCTYPE html><html><head><title>www.lg.com</title></head>"
                             "<body><div id='main-message'>This site can't be reached</div>"
                             "<div class='error-code'>ERR_PROXY_CONNECTION_FAILED</div>"
                             "</body></html>")

# Akamai's refusal, as lg.com/us served it on 2026-09-21 — note it never
# says "akamai", while every good page does.
AKAMAI_REFUSAL_FIXTURE_HTML = (
    "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n"
    "You don't have permission to access \"http://www.lg.com/us/tvs\" on this server.<P>\n"
    "Reference #18.58e1002.1790001857.20c1062e\n"
    "<P>https://errors.edgesuite.net/18.58e1002.1790001857.20c1062e</P>\n"
    "</BODY>\n</HTML>")


def check_parser_values():
    print("\n[parser: real fixture, asserted values]")
    rows = parse_products(PAGE_FIXTURE_HTML, "https://www.lg.com/ru/televisions",
                          category="televisions", page=1)
    # Two grid cards in, two rows out: the fixture also contains the site's
    # unfilled template row and two cards of the recommendation rail, and
    # both have to be thrown away.
    eq("two rows from the two grid cards", len(rows), 2)
    by_sku = {r.sku: r for r in rows}

    a = by_sku.get("OLED83W69LA")
    check("the first grid card parsed", a is not None)
    if a:
        eq("sku is LG's model code", a.sku, "OLED83W69LA")
        eq("title is the site's own long name", a.title,
           "83-дюймовый телевизор Smart TV Wallpaper TV LG OLED evo AI W6 4K 2026 года")
        eq("url", a.url, "https://www.lg.com/ru/televisions/lg-oled83w69la")
        eq("model id", a.model_id, "MD07610675")
        eq("sales code carries its market suffix", a.sales_model_code,
           "OLED83W69LA.ARUG")
        eq("model year", a.model_year, 2026)
        # The size switcher lists 83" and 77"; the ACTIVE one is this card's.
        eq("screen size is the active switcher, not the first", a.screen_size, "83")
        eq("and the siblings are recorded", a.sibling_sizes, '83", 77"')
        eq("currency comes from the microdata", a.currency, "RUB")
        # The microdata says price="0". A published zero is not a price.
        eq("a published zero is not written as a price", a.price, None)
        eq("no rating is invented from a zero", a.rating, None)
        eq("review count is a fact, and it is zero", a.review_count, 0)
        eq("category label from the run", a.category, "televisions")
        eq("page column", a.page, 1)
        eq("position column", a.position, 1)
        eq("provenance", a.price_source, "dom")
        check("image url is absolute",
              (a.image_url or "").startswith("https://www.lg.com/ru/images/"))
        # Both measured identical to the API's own values on all 12 cards of
        # page 2, which is why they are read from the card rather than left
        # null with the API as the only source.
        eq("the super category comes off the card", a.super_category,
           "tv-audio-video")
        eq("and so does the where-to-buy link, absolute", a.where_to_buy_url,
           "https://www.lg.com/ru/televisions/lg-oled83w69la#pdp_where")

    b = by_sku.get("55NU800B6LA")
    check("the second grid card parsed", b is not None)
    if b:
        eq("position is 1-based within the page", b.position, 2)
        eq("a card with no size switcher reports no screen size", b.screen_size, None)

    check("the unfilled template row is not a product",
          "*modelName*" in PAGE_FIXTURE_HTML and
          all(r.sku != "*modelName*" for r in rows))
    check("the recommendation rail is in the fixture but not in the rows",
          'class="products-list-group"' in PAGE_FIXTURE_HTML and len(rows) == 2)

    # page+position must be unique across a multi-page run: `position`
    # restarts at 1 on every page, so the column is worthless without the pair.
    page2 = parse_products(PAGE_FIXTURE_HTML, "https://www.lg.com/ru/televisions", page=2)
    pairs = [(r.page, r.position) for r in rows + page2]
    eq("page+position is unique across pages", len(set(pairs)), len(pairs))


def check_api_path_and_agreement():
    print("\n[the catalogue API, and whether it agrees with the grid]")
    payload = json.loads(API_FIXTURE_JSON)
    eq("the API's own product count is read", len(product_parser.api_products(payload)), 2)
    eq("and so is the number of products it pages through",
       product_parser.api_row_count(payload), 50)
    eq("and how many pages that is", product_parser.api_page_count(payload), 5)
    # The trap: two fields named alike, one of which is not a product count.
    eq("the count beside the product list is kept, and it is a different "
       "number", product_parser.api_variant_count(payload), 203)
    check("reading that one as the product count would report a complete run "
          "as having lost three quarters of the catalogue",
          product_parser.api_variant_count(payload)
          > 4 * product_parser.api_row_count(payload) - 1)
    past_end = {"data": [{"pageInfo": {"view": "N", "pageCount": 0},
                          "totalCount": 0, "productList": []}]}
    check("past the end the pagination block says nothing, and 0 pages is "
          "read as unknown rather than as 'no pages'",
          product_parser.api_page_count(past_end) is None
          and product_parser.api_row_count(past_end) is None)

    form = product_parser.parse_catalog_form(
        PAGE_FIXTURE_HTML, "https://www.lg.com/ru/televisions")
    check("the page's own form is found", form is not None)
    if form:
        eq("the API url comes from the form, not from a constant", form["url"],
           "https://www.lg.com/ru/mkt/ajax/category/retrieveCategoryProductList")
        eq("the category id comes from the form too",
           form["params"].get("categoryId"), "CT20206007")
        # Hardcoding this id would scrape RU televisions whatever URL was
        # asked for — CT20226005 is UA televisions, CT20206048 RU fridges.
        payload_page3 = product_parser.catalog_payload(form, 3)
        eq("the page number is what changes between requests",
           payload_page3.get("page"), "3")
        eq("and the other parameters are carried through unchanged",
           payload_page3.get("bizType"), "B2C")
    eq("the currency the page declares", 
       product_parser.declared_currency(PAGE_FIXTURE_HTML), "RUB")

    api_rows = {r.sku: r for r in product_parser.rows_from_api(
        payload, "https://www.lg.com/ru/televisions", category="televisions",
        page=1, currency="RUB")}
    dom_rows = {r.sku: r for r in parse_products(
        PAGE_FIXTURE_HTML, "https://www.lg.com/ru/televisions",
        category="televisions", page=1)}
    eq("both paths find the same products", sorted(api_rows), sorted(dom_rows))
    shared = ("title", "url", "model_id", "sales_model_code", "model_year",
              "currency", "price", "rating", "review_count", "image_url",
              "category", "page")
    disagreements = [(sku, f, getattr(api_rows[sku], f), getattr(dom_rows[sku], f))
                     for sku in api_rows for f in shared
                     if getattr(api_rows[sku], f) != getattr(dom_rows[sku], f)]
    check(f"and agree on every shared column ({len(shared)} columns x "
          f"{len(api_rows)} rows)" + (f" — {disagreements[:3]}" if disagreements else ""),
          not disagreements)
    eq("the API path labels its own provenance",
       {r.price_source for r in api_rows.values()}, {"api"})
    # Columns the card cannot carry, which is why the API path is preferred.
    first = api_rows["OLED83W69LA"]
    eq("the API row carries the site's own category name",
       first.product_category, "Телевизоры")
    eq("and the slug", first.product_category_slug, "televisions")
    eq("and the super-category", first.super_category, "tv-audio-video")
    eq("and the model status", first.status, "ACTIVE")


def check_no_price_is_invented():
    print("\n[prices: the ones that are not there]")
    # Measured 2026-09-21 across 36 products, two locales and two categories:
    # msrp, promotionPrice, obsSellingPrice, obsOriginalPrice, rPrice,
    # cheaperPrice and discountedRate are all 0 or null, and the site's own
    # price service (retrievePlpPriceSyncList) returns the same zeros. The
    # microdata publishes price="0" with priceCurrency="RUB".
    record = {"modelName": "X1", "modelUrlPath": "/ru/televisions/lg-x1",
              "userFriendlyName": "X1", "msrp": 0.0, "promotionPrice": 0.0,
              "obsSellingPrice": 0.0, "obsOriginalPrice": 0.0,
              "discountedRate": None, "reviewRatingStar2": 0.0,
              "reviewRating": 0}
    payload = {"data": [{"totalCount": 1, "productList": [record]}]}
    row = product_parser.rows_from_api(payload, "https://www.lg.com/ru/televisions",
                                       currency="RUB")[0]
    eq("a zero price is reported as no price", row.price, None)
    eq("a zero original price likewise", row.original_price, None)
    eq("a zero rating likewise", row.rating, None)
    eq("but the declared currency survives", row.currency, "RUB")
    # And a real price, if this platform ever publishes one, must come out.
    record["obsSellingPrice"] = 149990.0
    record["obsOriginalPrice"] = 179990.0
    row = product_parser.rows_from_api(payload, "https://www.lg.com/ru/televisions",
                                       currency="RUB")[0]
    eq("a real price is reported", row.price, 149990.0)
    eq("and a real original price", row.original_price, 179990.0)


def check_pagination_convention():
    print("\n[pagination]")
    # Measured 2026-09-21: pages 1, 2 and 3 of /ru/televisions each served a
    # different set of grid models, so ?page= is real rather than ignored.
    eq("page 1 carries no page parameter",
       page_url("https://www.lg.com/ru/televisions", 1),
       "https://www.lg.com/ru/televisions")
    eq("page 2 uses the site's own parameter",
       page_url("https://www.lg.com/ru/televisions", 2),
       "https://www.lg.com/ru/televisions?page=2")
    eq("a stale page parameter is replaced, not appended to",
       page_url("https://www.lg.com/ru/televisions?page=7", 3),
       "https://www.lg.com/ru/televisions?page=3")
    eq("other query parameters survive",
       page_url("https://www.lg.com/ru/televisions?sort=price", 2),
       "https://www.lg.com/ru/televisions?sort=price&page=2")
    eq("category label from the path",
       category_from_url("https://www.lg.com/ru/televisions"), "televisions")
    eq("a locale segment is not a category",
       category_from_url("https://www.lg.com/ru/"), None)
    eq("hyphens become spaces",
       category_from_url("https://www.lg.com/ru/washing-machines"), "washing machines")


def check_supported_locales():
    print("\n[locales: two, by the site's own reckoning]")
    # The hreflang set on /ru/televisions has exactly two entries, ru-ru and
    # ru-ua, and /ua serves the same platform. /uk is a different markup
    # generation with no data-model-* at all, and /us answered 403 from
    # Akamai to the same client that /ru served.
    eq("the supported set", sorted(product_parser.SUPPORTED_LOCALES), ["ru", "ua"])
    eq("a ru URL is accepted",
       product_parser.unsupported_locale_reason("https://www.lg.com/ru/televisions"), None)
    eq("a ua URL is accepted",
       product_parser.unsupported_locale_reason("https://www.lg.com/ua/televisions"), None)
    for locale in ("uk", "us", "de"):
        reason = product_parser.unsupported_locale_reason(
            f"https://www.lg.com/{locale}/tvs")
        check(f"an {locale} URL is refused", reason is not None)
        check(f"and the refusal for {locale} says WHY, naming the platform "
              f"difference rather than calling it 'not an LG site'",
              reason and "different platform" in reason)
    reason = product_parser.unsupported_locale_reason("https://example.com/ru/televisions")
    check("another host entirely is refused as such",
          reason and "is not lg.com" in reason)
    check("and the locale itself is readable for a log",
          product_parser.locale_from_url("https://www.lg.com/ua/televisions") == "ua")


def check_page_states():
    print("\n[page_flow]")
    content = page_flow.classify(PAGE_FIXTURE_HTML, page_num=1)
    eq("a page with grid products is content", content.state, page_flow.CONTENT)
    check("content is not retried", not content.policy.retry)

    # Past the end the site answers 200 with an empty grid — the same shape
    # as an empty category. Only the page number tells them apart, and they
    # are different answers: complete versus exit 4.
    # The shape page 99 of /ru/televisions really has: the site's assets, the
    # filter form and the grid container, and not one card inside it.
    served_but_empty = ("<html><head>" +
                        '<link href="/lg5-common-gp/x.css">' * 10 +
                        "</head><body><form id='categoryFilterForm'></form>"
                        "<div class='product-list-box'></div></body></html>")
    past_end = page_flow.classify(served_but_empty, page_num=9)
    eq("an empty grid on page 9 is exhausted", past_end.state, page_flow.EXHAUSTED)
    check("and that is a complete answer", past_end.policy.complete)
    empty = page_flow.classify(served_but_empty, page_num=1)
    eq("the same page as page 1 is an empty category", empty.state, page_flow.EMPTY)
    check("also complete, but it is exit 4 rather than rows", empty.policy.complete)

    blocked = page_flow.classify(AKAMAI_REFUSAL_FIXTURE_HTML)
    eq("Akamai's refusal is blocked", blocked.state, page_flow.BLOCKED)
    eq("and names the vendor", blocked.vendor, "akamai")
    eq("a refusal status alone is enough",
       page_flow.classify("<html>x</html>", status_code=403).state, page_flow.BLOCKED)
    error_page = page_flow.classify(CHROME_ERROR_FIXTURE_HTML)
    eq("Chromium's own error page is blocked, despite carrying the site's "
       "hostname in its title", error_page.state, page_flow.BLOCKED)

    # The rule that would have broken this repo: count a marker on a page you
    # KNOW is good. lg.com writes "akamai" once on every page it serves and
    # not at all on the refusal.
    good = PAGE_FIXTURE_HTML
    firing = [m for markers in product_parser.BOT_CHALLENGE_MARKERS.values()
              for m in markers if m in good]
    check("no challenge marker appears on a good page"
          + (f" — {firing} does" if firing else ""), not firing)
    fires_on_refusal = [m for markers in product_parser.BOT_CHALLENGE_MARKERS.values()
                        for m in markers if m in AKAMAI_REFUSAL_FIXTURE_HTML]
    check(f"and {len(fires_on_refusal)} of them fire on the real refusal",
          len(fires_on_refusal) >= 2)
    check("(measured: the word 'akamai' is on every good page and absent "
          "from the refusal, which is why it is not a marker)",
          "akamai" not in AKAMAI_REFUSAL_FIXTURE_HTML.lower())

    api_state = page_flow.classify("", record_count=12, total_results=203)
    eq("an API response with records is content", api_state.state, page_flow.CONTENT)
    eq("and carries the category's own total", api_state.total_results, 203)

    # A JSON payload carries no asset paths and no challenge wording. Weighing
    # HTML evidence against it called every empty API page a block, which the
    # engine then read as "nothing new here" and reported as a complete run.
    api_empty = page_flow.classify("", record_count=0, page_num=1, total_results=0)
    eq("an API page with no products is empty, not blocked",
       api_empty.state, page_flow.EMPTY)
    api_past_end = page_flow.classify("", record_count=0, page_num=4,
                                      total_results=203)
    eq("and past the end of the category it is exhausted",
       api_past_end.state, page_flow.EXHAUSTED)
    check("neither is ever reported as a block",
          not api_empty.policy.blocked and not api_past_end.policy.blocked)

    # The shell: LG's own assets, none of the grid's machinery. Measured on
    # the real pages — a served category page carries `categoryFilterForm`
    # and `product-list-box` whether or not it holds a single product, so a
    # response without them has not painted the grid yet.
    shell = page_flow.classify(SHELL_FIXTURE_HTML, page_num=1)
    eq("the app shell is unpainted, not an empty category",
       shell.state, page_flow.UNPAINTED)
    check("so the engines wait and read it again rather than exiting 4",
          shell.policy.retry and shell.policy.wait_first)
    check("an empty response is unpainted too",
          page_flow.classify("").state == page_flow.UNPAINTED)
    check("(measured on the real pages: a good page and page 99, which holds "
          "no cards at all, both carry that machinery)",
          product_parser.has_category_machinery(PAGE_FIXTURE_HTML)
          and product_parser.has_category_machinery(served_but_empty))


def check_known_limitations_are_pinned():
    print("\n[pinned limitations]")
    # Where a defence would be worse than the gap, the CURRENT behaviour is
    # asserted with the reason, so a future change is a decision rather than
    # a surprise.
    #
    # 1. A card with no size switcher reports no screen size. The API has the
    #    inch code for those products; the card does not, and guessing it out
    #    of the title ("55-дюймовый…") would be a parser inventing data.
    rows = {r.sku: r for r in parse_products(PAGE_FIXTURE_HTML,
                                             "https://www.lg.com/ru/televisions")}
    eq("the DOM path leaves screen_size null where the card has no switcher",
       rows["55NU800B6LA"].screen_size, None)
    api_rows = {r.sku: r for r in product_parser.rows_from_api(
        json.loads(API_FIXTURE_JSON), "https://www.lg.com/ru/televisions")}
    eq("while the API path has it for the same product",
       api_rows["55NU800B6LA"].screen_size, "55")
    # 2. The energy label is published on EU locales and null on these two.
    eq("energy_label is null on this platform", api_rows["55NU800B6LA"].energy_label, None)


def check_shortfall_arithmetic():
    print("\n[page coverage]")
    # The category publishes its own product count, so "did this page bring
    # back everything it should have?" is arithmetic rather than a
    # threshold. 203 products at 12 per page is 16 full pages and an
    # 11-product seventeenth.
    eq("a full page is 12", page_flow.expected_records(203, 1), 12)
    eq("the last page holds the remainder", page_flow.expected_records(203, 17), 11)
    eq("a page past the end expects nothing", page_flow.expected_records(203, 18), 0)
    eq("no total means no expectation", page_flow.expected_records(None, 1), None)
    # Past the end the API reports totalCount 0 — that describes the
    # response, not the category, so it must not become an expectation.
    eq("a zero total is not an expectation", page_flow.expected_records(0, 2), None)
    check("a full page reports no shortfall",
          page_flow.shortfall(203, 1, 12) is None)
    message = page_flow.shortfall(203, 1, 7)
    check("a short page is named, with both numbers in the message",
          message and "7" in message and "12" in message)


def check_fingerprint_kwargs_are_ones_playwright_accepts():
    print("\n[fingerprint kwargs]")
    # An unknown key in new_context(**kwargs) is a TypeError at launch, on the
    # PAID path, at runtime. Checked against the driver's real
    # signature rather than against a list written from memory.
    import fingerprint_client
    sample = {"id": "x", "country": "de", "userAgent": {"value": "UA/1"},
              "screen": {"width": 1920, "height": 1080}, "locale": "de-DE",
              "timezone": "Europe/Berlin",
              "navigator": {"platform": "Win32", "hardwareConcurrency": 8},
              "webgl": {"vendor": "Google Inc.", "renderer": "ANGLE"}}
    kwargs = fingerprint_client.playwright_context_kwargs(sample)
    check("the fingerprint produces some context kwargs at all", bool(kwargs))
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        SKIPPED_GROUPS.append("fingerprint kwargs (playwright not installed)")
        check("fingerprint kwargs vs the real new_context signature "
              "(playwright not installed — SKIPPED)", True)
        return
    from playwright.sync_api import Browser
    accepted = set(inspect.signature(Browser.new_context).parameters)
    unknown = sorted(set(kwargs) - accepted)
    check("every fingerprint kwarg is one Browser.new_context accepts"
          + (f" (unknown: {unknown})" if unknown else ""), not unknown)
    # And the init script must be valid JS shape, since a syntax error there
    # fails silently inside the browser.
    script = fingerprint_client.playwright_init_script(sample)
    check("the init script bakes its values in as JSON",
          '"platform": "Win32"' in script or '"platform":"Win32"' in script)
    check("and patches both WebGL contexts",
          "WebGL2RenderingContext" in script and "37446" in script)


def check_captcha_detection():
    print("\n[captcha]")
    # Nine captures across two locales and three categories carry no captcha
    # markup of any kind on a category page.
    eq("no challenge is detected on a category page",
       captcha_solver.detect_in_html(PAGE_FIXTURE_HTML,
                                     "https://www.lg.com/ru/televisions"), None)

    support = captcha_solver.detect_in_html(
        SUPPORT_FIXTURE_HTML, "https://www.lg.com/ru/support/email-to-ceo")
    check("the support form's reCAPTCHA is detected", support is not None)
    if support:
        eq("and classified as v3", support.kind, "recaptcha_v3")
        eq("with its sitekey", support.sitekey, "6LFIXTUREFIXTUREFIXTUREFIXTURE12")
        eq("and mapped to the right paid task type",
           captcha_solver._task_for(support, 0.7)["type"], "RecaptchaV3TaskProxyless")

    # The empty <captcha-widgets> that appears in captures taken through the
    # Scraping Browser belongs to the AUTOSOLVER EXTENSION, not to lg.com:
    # loading the same pages in a plain Chromium with no extensions finds it
    # nowhere. Detecting a FILLED one is still worth it.
    check("a filled autosolver mount is reported",
          captcha_solver.detect_autosolver_mount(SUPPORT_FIXTURE_HTML) is not None)
    eq("and an absent one is not a challenge",
       captcha_solver.detect_autosolver_mount(PAGE_FIXTURE_HTML), None)

    check("an arrow-function injector ships for Playwright/pyppeteer",
          captcha_solver.INJECT_TOKEN_FN.strip().startswith("(token) =>"))
    check("a function-body injector ships for Selenium",
          "arguments[0]" in captcha_solver.INJECT_TOKEN_BODY)
    v3 = captcha_solver.CaptchaChallenge(kind="recaptcha_v3", sitekey="6L" + "a" * 30,
                                         page_url="https://www.lg.com/ru/x")
    eq("an out-of-range minScore is snapped to a documented one",
       captcha_solver._task_for(v3, 0.55)["minScore"], 0.7)


def check_credentials_never_leak():
    print("\n[credentials]")
    masked = proxy_pool.mask("http://user:secret@gate.example.com:9999")
    check("a masked proxy keeps host and port", "gate.example.com:9999" in masked)
    check("and drops the credentials",
          "secret" not in masked and "user" not in masked)
    pw = proxy_pool.to_playwright("http://user:secret@gate.example.com:9999")
    check("credentials never reach the browser's argv",
          "secret" not in pw["server"] and "user" not in pw["server"])
    eq("they go in their own fields instead", (pw["username"], pw["password"]),
       ("user", "secret"))
    arg, creds = proxy_pool.to_pyppeteer("http://user:secret@gate.example.com:9999")
    check("pyppeteer's switch carries no credentials", "secret" not in arg)
    eq("they go through page.authenticate instead", creds["password"], "secret")
    arg, warning = proxy_pool.to_selenium("http://user:secret@gate.example.com:9999")
    check("Selenium's switch carries no credentials", "secret" not in arg)
    check("and the user is WARNED rather than misled",
          warning and "cannot authenticate" in warning)

    # An exception message is a log. The v1 captcha endpoint and the
    # fingerprint API both take the key as a query parameter.
    leaky = ("HTTPSConnectionPool: /res.php?key=0123456789abcdef0123456789abcdef"
             "&action=get failed; also ws://login:pass@cb.2captcha.com:9222")
    redacted = proxy_pool.redact_secret_patterns(leaky)
    check("a key in a query string is redacted",
          "0123456789abcdef" not in redacted)
    check("a password in a URL is redacted", "pass@" not in redacted)
    check("the endpoint itself survives redaction",
          "res.php" in redacted and "cb.2captcha.com:9222" in redacted)
    # Globally, not once.
    twice = proxy_pool.redact_secret_patterns("key=aaa and key=bbb")
    check("every occurrence is redacted, not just the first",
          "aaa" not in twice and "bbb" not in twice)

    for bad, why in (("gate.example.com:9999", "a bare host:port is refused"),
                     ("socks5://user:pass@host:1080",
                      "an authenticated socks5 exit is refused, not silently stripped")):
        try:
            proxy_pool.parse_proxy_line(bad)
            check(why, False)
        except proxy_pool.ProxyError as e:
            check(why, True)
            check("and the refusal itself carries no credentials",
                  "pass" not in str(e).replace("password", ""))


def check_remote_connect_failures_are_redacted():
    print("\n[remote connect failures]")
    # Measured live on 2026-09-19 against a real Scraping Browser endpoint
    # that answered 401: Playwright put the endpoint — password included —
    # into the exception message AND into a four-line call log under it, five
    # occurrences in one traceback. An exception message is a log, and a
    # traceback printed on the way out is a log too.
    secret = "ws://acct-zone-scraping_browser-pid-1:s3cr3tpassw0rd@cb.2captcha.com:9222"
    library_error = ("connect_over_cdp: WebSocket error\n"
                     f"  - <ws unexpected response> {secret}/ 401 Unauthorized\n"
                     f"  - <ws error> {secret}/ closed before established\n"
                     f"  - <ws connect error> {secret}/ closed\n")
    redacted = proxy_pool.redact_secret_patterns(library_error)
    check("a credentialled endpoint is redacted out of a library error",
          "s3cr3tpassw0rd" not in redacted)
    eq("every occurrence of it, not just the first",
       redacted.count("***:***@cb.2captcha.com:9222"), 3)
    check("and the endpoint, the host and the status all survive",
          "cb.2captcha.com:9222" in redacted and "401 Unauthorized" in redacted)

    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} wraps its remote connect rather than letting the "
              f"library's own error escape",
              "redact_secret_patterns(str(e))" in source)
        check(f"{name} redacts a traceback before printing it",
              "redact_secret_patterns(traceback.format_exc())" in source)

    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        check("the engine's own connect path redacts (playwright not "
              "installed — SKIPPED)", True)
        return

    class _StubChromium:
        def connect_over_cdp(self, endpoint, timeout=None):
            raise RuntimeError(library_error)

    class _StubPW:
        chromium = _StubChromium()

    class Args:
        cdp_endpoint = secret

    try:
        engine._connect_remote(_StubPW(), Args())
        check("the engine refuses a failed connect", False)
    except RuntimeError as e:
        check("the engine's own connect failure carries no password",
              "s3cr3tpassw0rd" not in str(e))
        check("and still names what failed and where",
              "cb.2captcha.com:9222" in str(e))


def check_proxy_rotation():
    print("\n[proxy rotation]")
    pool = proxy_pool.ProxyPool(["http://a:1", "http://b:2", "http://c:3"],
                                rotate="per-page")
    eq("starts on the first exit", pool.current, "http://a:1")
    pool.advance("test")
    eq("advances in order", pool.current, "http://b:2")
    pool.advance("test")
    pool.advance("test")
    eq("wraps rather than exhausting", pool.current, "http://a:1")
    eq("counts its rotations", pool.rotations, 3)
    single = proxy_pool.ProxyPool(["http://a:1"])
    single.advance("nowhere to go")
    eq("a one-exit pool stays put", single.current, "http://a:1")
    eq("and does not claim a rotation", single.rotations, 0)
    check("per-run does not rotate per page", not single.rotates_per_page())
    copy = pool.proxies
    copy.append("http://d:4")
    eq("the pool hands out a copy, so a worker cannot mutate it", len(pool), 3)


# ---------------------------------------------------------------------------
# 4. Output contract
# ---------------------------------------------------------------------------
def _row(**kw):
    base = dict(sku="1", url="https://www.lg.com/ru/televisions/lg-x1", price=1.0)
    base.update(kw)
    return Product(**base)


def check_proxy_preflight():
    print("\n[proxy preflight]")
    # Measured 2026-09-21: an exit whose password had been rotated answered
    # 407 in 0.2s to a plain request, while the SAME exit under Chromium gave
    # nothing but navigation timeouts — three 60s attempts per page, with the
    # proxy never mentioned. The family rule is that a proxy failure is not a
    # timeout; this is the case where the browser reports one anyway, so the
    # exit is checked before a browser is involved.
    eq("407 is credentials, not slowness",
       proxy_pool.classify_preflight("Tunnel connection failed: 407 Proxy "
                                     "Authentication Required"),
       proxy_pool.PREFLIGHT_REJECTED)
    eq("a refused tunnel is an unusable exit",
       proxy_pool.classify_preflight("ProxyError('Unable to connect to proxy', "
                                     "ConnectionRefusedError)"),
       proxy_pool.PREFLIGHT_UNUSABLE)
    eq("a read timeout is not blamed on the exit",
       proxy_pool.classify_preflight("Read timed out. (read timeout=15)"),
       proxy_pool.PREFLIGHT_SLOW)
    eq("and an unrecognised failure is not either",
       proxy_pool.classify_preflight("something nobody has seen yet"),
       proxy_pool.PREFLIGHT_SLOW)

    # A rejected exit ends the run as bad usage; a slow one only warns.
    pool = proxy_pool.ProxyPool(["http://user:pass@gate.example.com:9999"])
    real = proxy_pool.preflight
    try:
        proxy_pool.preflight = lambda url, target, timeout=15.0: (
            proxy_pool.PREFLIGHT_REJECTED, "407 Proxy Authentication Required")
        try:
            proxy_pool.check_exit_or_raise(pool, "https://www.lg.com/ru/televisions")
            check("a rejected exit stops the run", False)
        except proxy_pool.ProxyError as e:
            check("a rejected exit stops the run before a browser starts", True)
            check("and the message says it is the exit, not the site",
                  "not the site" in str(e))
            check("and carries no credentials", "pass@" not in str(e))
        proxy_pool.preflight = lambda url, target, timeout=15.0: (
            proxy_pool.PREFLIGHT_SLOW, "read timed out")
        proxy_pool.check_exit_or_raise(pool, "https://www.lg.com/ru/televisions")
        check("a slow exit only warns — the target may be what is slow", True)
        proxy_pool.preflight = lambda url, target, timeout=15.0: (
            proxy_pool.PREFLIGHT_OK, "HTTP 200")
        proxy_pool.check_exit_or_raise(pool, "https://www.lg.com/ru/televisions")
        check("a working exit says so and continues", True)
        proxy_pool.check_exit_or_raise(None, "https://www.lg.com/ru/televisions")
        check("and with no pool there is nothing to check", True)
    finally:
        proxy_pool.preflight = real

    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} preflights its exit before launching a browser",
              "check_exit_or_raise(pool, args.url)" in source)


def check_output_contract():
    print("\n[output contract]")
    columns = list(asdict(Product()).keys())
    eq("the family prefix leads the schema, in order", columns[:11],
       ["source", "scraped_at", "url", "sku", "title", "price", "currency",
        "original_price", "discount_pct", "category", "price_source"])
    eq("source names the site", Product().source, "lg.com")
    for gone in ("brand", "in_stock"):
        check(f"{gone!r} is not a column (measured null on every row)",
              gone not in columns)

    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        # An empty result still carries a header row.
        write_csv([], f"{prefix}.csv")
        with open(f"{prefix}.csv", newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        eq("an empty CSV still carries its header", header, columns)

        # A run that finds nothing writes nothing.
        with open(f"{prefix}.json", "w", encoding="utf-8") as f:
            f.write('[{"sku": "yesterday"}]')
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = save([], prefix, "json")
        eq("an empty run exits 4", rc, EXIT_NO_PRODUCTS)
        eq("and leaves last night's good output alone",
           json.load(open(f"{prefix}.json", encoding="utf-8")), [{"sku": "yesterday"}])
        with redirect_stdout(io.StringIO()):
            rc = save([], prefix, "json", allow_empty=True)
        eq("--allow-empty writes the empty result",
           json.load(open(f"{prefix}.json", encoding="utf-8")), [])
        eq("and still exits 4", rc, EXIT_NO_PRODUCTS)

        # A failed run writes no sidecar beside the previous good output.
        os.remove(f"{prefix}.json")
        with redirect_stdout(io.StringIO()):
            rc = finish_run([], prefix, "json", False, blocked=True,
                            stop_reason="blocked_cloudflare", pages_requested=1,
                            pages_completed=0, start_url="u", final_url="u")
        eq("a blocked run exits 3", rc, EXIT_BLOCKED)
        check("and writes no sidecar", not os.path.exists(f"{prefix}.meta.json"))

        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="page_load_timeout", pages_requested=3,
                            pages_completed=1, pages_failed=[2],
                            start_url="u", final_url="u")
        eq("a run that stopped early exits 6", rc, EXIT_PARTIAL)
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("the sidecar says partial", meta["status"], "partial")
        eq("and names WHICH pages failed, not just how many",
           meta["pages_failed"], [2])
        eq("and records the site's own result count", "total_results" in meta, True)

        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="listing_exhausted", pages_requested=9,
                            pages_completed=2, start_url="u", final_url="u",
                            total_results=42, addressable=True)
        eq("running out of listings is a complete run", rc, 0)
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("the sidecar says complete", meta["status"], "complete")
        eq("and carries the site's own total", meta["total_results"], 42)
        eq("and whether pages could be addressed by URL", meta["addressable"], True)

    seen = set()
    rows = dedupe_by_sku([_row(sku="a"), _row(sku="b"), _row(sku="a")], seen)
    eq("duplicates within a page are dropped", [r.sku for r in rows], ["a", "b"])
    rows = dedupe_by_sku([_row(sku="b"), _row(sku="c")], seen)
    eq("and across pages, through the shared set()", [r.sku for r in rows], ["c"])
    rows = dedupe_by_sku([_row(sku=None), _row(sku=None)], set())
    eq("a row with no sku is never dropped as a duplicate", len(rows), 2)


# ---------------------------------------------------------------------------
# 5. The engines: parity, and the five checks §17 says to steal
# ---------------------------------------------------------------------------
# The flag contract every engine in the family exposes, as
# argparse destinations. Asserted in BOTH directions: a missing flag fails,
# and so does an engine growing one its twins do not have.
CONTRACT_FLAGS = {
    "url", "pages", "category", "format", "out", "delay", "retries",
    "retry_delay", "concurrency", "proxy", "proxy_file", "proxy_rotate",
    "proxy_shuffle", "proxy_block_retries", "twocaptcha_key", "captcha_api",
    "solve_captcha", "min_score", "cdp_endpoint", "allow_empty", "dump_html",
    "headless", "fingerprint", "fp_tags", "fp_country",
}


def _engine_flags(module):
    """The destinations `module.parse_args()` produces, via the real entry point.

    Exercised through parse_args rather than by reading add_argument calls:
    a signature that drifts from its callers is invisible to a test that only
    touches the internals underneath.
    """
    argv = sys.argv
    sys.argv = [module.__name__, "--url", "https://www.lg.com/ru/televisions"]
    try:
        return set(vars(module.parse_args()))
    finally:
        sys.argv = argv


def check_engine_parity():
    print("\n[engine parity]")
    if not ENGINES:
        check("engine parity (no engine library installed here — SKIPPED)", True)
        return
    flag_sets = {}
    for name, module in ENGINES.items():
        flags = _engine_flags(module)
        flag_sets[name] = flags
        missing = CONTRACT_FLAGS - flags
        extra = flags - CONTRACT_FLAGS
        check(f"{name} carries every flag in the contract"
              + (f" (missing {sorted(missing)})" if missing else ""), not missing)
        check(f"{name} adds no flag outside the contract"
              + (f" (extra {sorted(extra)})" if extra else ""), not extra)
    if len(flag_sets) > 1:
        first = next(iter(flag_sets.values()))
        check("every engine exposes the SAME flags as its twins",
              all(s == first for s in flag_sets.values()))

    for name, module in ENGINES.items():
        eq(f"{name} uses the shared card selector", module.ITEM_CARD_SELECTOR,
           product_parser.SELECTORS["item_card"])
        check(f"{name}'s MIN_CARD_MATCHES is > 1 (one match resolves on an "
              f"unrelated element long before the grid paints)",
              module.MIN_CARD_MATCHES > 1)
    if len(ENGINES) > 1:
        values = {m.MIN_CARD_MATCHES for m in ENGINES.values()}
        eq("every engine agrees on MIN_CARD_MATCHES", len(values), 1)


def check_readiness_wait_is_csp_safe():
    print("\n[CSP-safe readiness]")
    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} does not wait on an evaluated string "
              f"(wait_for_function would break under a CSP without unsafe-eval)",
              not re.search(r"\.wait_for_function\s*\(", source))


def check_engine_imports_driver_at_module_level():
    print("\n[driver imports]")
    # An engine that imports its driver inside the launch path imports cleanly
    # with the driver absent: the offline suite's group never skips, and the
    # CI job that exists to fail on an unexpected skip cannot catch a broken
    # import. This drifts back silently, so it is asserted.
    expected = {"playwright_scraper": "playwright",
                "puppeteer_scraper": "pyppeteer",
                "selenium_scraper": "selenium"}
    for name, driver in expected.items():
        tree = ast.parse(open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read())
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        found = any(
            (isinstance(n, ast.ImportFrom) and (n.module or "").startswith(driver))
            or (isinstance(n, ast.Import) and any(a.name.startswith(driver) for a in n.names))
            for n in top_level)
        check(f"{name} imports {driver} at module level", found)


def _module_sources():
    for name in sorted(os.listdir(REPO)):
        if name.endswith(".py") and name != "smoke_test.py":
            yield name, open(os.path.join(REPO, name), encoding="utf-8").read()


def check_no_undefined_names():
    print("\n[undefined names]")
    # compileall proves a file PARSES, not that its names RESOLVE. A live run
    # elsewhere in this family died with NameError on a line reached only
    # while fetching, after an import had been removed — invisible to import,
    # --help, compileall and 400 green assertions. Deliberately coarse (one
    # pool of bindings, no scope tracking) so it under-reports rather than
    # inventing problems.
    import builtins
    for name, source in _module_sources():
        tree = ast.parse(source)
        bound = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    for arg in (a.posonlyargs + a.args + a.kwonlyargs
                                + ([a.vararg] if a.vararg else [])
                                + ([a.kwarg] if a.kwarg else [])):
                        bound.add(arg.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Lambda):
                a = node.args
                for arg in (a.posonlyargs + a.args + a.kwonlyargs
                            + ([a.vararg] if a.vararg else [])
                            + ([a.kwarg] if a.kwarg else [])):
                    bound.add(arg.arg)
            elif isinstance(node, ast.comprehension):
                for target in ast.walk(node.target):
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
            elif isinstance(node, ast.Global):
                bound.update(node.names)
        used = {n.id for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        unknown = sorted(used - bound)
        check(f"{name}: every name it loads is imported, defined or assigned"
              + (f" (unknown: {unknown})" if unknown else ""), not unknown)


def check_shared_calls_bind():
    print("\n[shared call signatures]")
    # The check that found six broken call sites in one pass on another repo:
    # `classify(html, status, url)` took `status` positionally while two of
    # three engines called it `classify(html, url=...)`, and both crashed on
    # their FIRST fetch — invisible to import, --help, compileall and the
    # undefined-name walk above, because none of those calls a function the
    # way a live run does.
    shared_modules = {"product_parser": product_parser, "page_flow": page_flow,
                      "output_writer": output_writer, "captcha_solver": captcha_solver,
                      "proxy_pool": proxy_pool, "env_config": env_config}
    placeholder = object()
    for name, source in _module_sources():
        tree = ast.parse(source)
        # local name -> callable, for `from x import y` and `import x`
        callables, modules = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in shared_modules:
                for alias in node.names:
                    target = getattr(shared_modules[node.module], alias.name, None)
                    if callable(target):
                        callables[alias.asname or alias.name] = target
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in shared_modules:
                        modules[alias.asname or alias.name] = shared_modules[alias.name]
        problems = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = None
            if isinstance(node.func, ast.Name):
                target = callables.get(node.func.id)
            elif (isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id in modules):
                target = getattr(modules[node.func.value.id], node.func.attr, None)
                if not callable(target):
                    target = None
            if target is None or isinstance(target, type):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or \
                    any(k.arg is None for k in node.keywords):
                continue    # *args/**kwargs: nothing to bind against
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                continue
            try:
                signature.bind(*([placeholder] * len(node.args)),
                               **{k.arg: placeholder for k in node.keywords})
            except TypeError as e:
                problems.append(f"line {node.lineno}: {getattr(target, '__name__', target)} {e}")
        check(f"{name}: every call into a shared module binds against its real "
              f"signature" + (f" — {problems}" if problems else ""), not problems)


def check_banned_wording():
    print("\n[wording]")
    # Built from pieces so this file does not itself contain the banned
    # strings it is scanning for.
    banned = ["anti" + "detect", "cloud " + "browser", "gate." + "2prx.com",
              "ANTI" + "DETECT_LOCAL_API"]
    scanned = 0
    hits = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "captures", "legacy", ".venv"}]
        for filename in files:
            if not filename.endswith((".py", ".md", ".yml", ".yaml", ".txt", ".example", ".toml")):
                continue
            if filename == "smoke_test.py":
                continue
            path = os.path.join(root, filename)
            text = open(path, encoding="utf-8", errors="replace").read().lower()
            scanned += 1
            for phrase in banned:
                if phrase.lower() in text:
                    hits.append(f"{os.path.relpath(path, REPO)}: {phrase!r}")
    check(f"no shipped file uses a banned product name ({scanned} files scanned)"
          + (f" — {hits}" if hits else ""), not hits)
    readme = os.path.join(REPO, "README.md")
    if os.path.exists(readme):
        text = open(readme, encoding="utf-8").read()
        check("the README names the Scraping Browser API",
              "Scraping Browser API" in text)


def check_removed_flags_stay_removed():
    print("\n[removed flags]")
    # Scoped to the ENGINES: --country is banned on a scraper (it could
    # disagree with the URL) and legitimate on fingerprint_client.py, where it
    # picks a fingerprint's locale.
    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper",
                 "scraper_api_client"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} does not reintroduce --country",
              '"--country"' not in source)
        check(f"{name} does not reintroduce the removed local-solver flag",
              '"--' + "antidetect" + '"' not in source)
    fingerprint = open(os.path.join(REPO, "fingerprint_client.py"), encoding="utf-8").read()
    check("fingerprint_client.py DOES still have --country (it picks a "
          "fingerprint's locale, which is a different thing)",
          '"--country"' in fingerprint)
    check("and defaults --fp-tags to ONE OS-family tag, which is what the API "
          "accepts", all('"--fp-tags", default="Windows"' in
                         open(os.path.join(REPO, f"{n}.py"), encoding="utf-8").read()
                         for n in ("playwright_scraper", "puppeteer_scraper",
                                   "selenium_scraper")))


def check_env_example_matches_code():
    print("\n[.env.example]")
    example_path = os.path.join(REPO, ".env.example")
    if not os.path.exists(example_path):
        check(".env.example exists", False)
        return
    documented = set(env_config.documented_keys(example_path))
    known = set(env_config.ENV_KEYS)
    eq("every documented variable is read by the code", documented - known, set())
    eq("every variable the code reads is documented", known - documented, set())

    # Round-trip a COPIED example through the real loader: every credential
    # must read as unset. The braced placeholders 2Captcha's own docs use
    # ({login}, {password}) once sailed through a literal-only check and
    # produced a 401 a long way from its cause.
    saved = {k: os.environ.get(k) for k in known}
    try:
        for line in open(example_path, encoding="utf-8"):
            parsed = env_config._parse_line(line)
            if parsed:
                os.environ[parsed[0]] = parsed[1]
        credentials = {k for k in known
                       if "KEY" in k or "PROXY" in k or "CDP" in k}
        for key in sorted(credentials):
            eq(f"{key} from a copied .env.example reads as unset",
               env_config.env_value(key), None)
        # The other half of the same check: a NON-credential default must
        # survive being copied, or the example is useless. FLIPPA_URL is a
        # real listing URL on purpose.
        for key in sorted(known - credentials):
            check(f"{key} from a copied .env.example is still usable",
                  bool(env_config.env_value(key)))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    check("an empty value is unset and is NOT warned about "
          "(an unset CI secret arrives empty)", env_config.is_placeholder(""))
    check("a braced vendor example is treated as unset",
          env_config.is_placeholder("ws://{login}-zone-x:{password}@cb.2captcha.com:9222"))
    check("a real credentialled URL is NOT treated as a placeholder",
          not env_config.is_placeholder("ws://real-login:realpass@cb.2captcha.com:9222"))


def check_env_precedence():
    print("\n[.env precedence]")
    class Args:
        twocaptcha_key = None
        url = None
    saved = os.environ.get("TWOCAPTCHA_KEY")
    try:
        os.environ["TWOCAPTCHA_KEY"] = "from-the-environment"
        args = Args()
        env_config.apply(args, keys={"TWOCAPTCHA_KEY": "twocaptcha_key"}, quiet=True)
        eq("an unset flag is filled from the environment",
           args.twocaptcha_key, "from-the-environment")
        args = Args()
        args.twocaptcha_key = "typed-on-the-command-line"
        env_config.apply(args, keys={"TWOCAPTCHA_KEY": "twocaptcha_key"}, quiet=True)
        eq("an explicit flag always wins", args.twocaptcha_key,
           "typed-on-the-command-line")
    finally:
        if saved is None:
            os.environ.pop("TWOCAPTCHA_KEY", None)
        else:
            os.environ["TWOCAPTCHA_KEY"] = saved


def check_policy_constants_have_consumers():
    print("\n[policy constants]")
    # A policy constant nothing reads is the same defect as dead code, and
    # harder to see, because the prose around it reads like enforcement
    #.
    # A consumer may reach the constant through an accessor its own module
    # exposes — `state.policy` reads STATE_POLICY — so each entry names the
    # spellings that count as reading it. What is NOT allowed is a constant
    # with a paragraph of justification and no reader at all, or a second
    # copy of its values somewhere else (which is how `--proxy-rotate`'s
    # choices drifted away from ROTATE_MODES).
    constants = {
        "STATE_POLICY": ("page_flow.py", ("STATE_POLICY", ".policy")),
        "MIN_ASSET_REFERENCES": ("product_parser.py", ("MIN_ASSET_REFERENCES",)),
        "BOT_CHALLENGE_MARKERS": ("product_parser.py",
                                  ("BOT_CHALLENGE_MARKERS", "detect_bot_challenge")),
        "COMPLETE_STOP_REASONS": ("output_writer.py",
                                  ("COMPLETE_STOP_REASONS", "finish_run")),
        "ROTATE_MODES": ("proxy_pool.py", ("ROTATE_MODES",)),
        "ENV_KEYS": ("env_config.py", ("ENV_KEYS", "env_config.apply")),
    }
    for constant, (home, spellings) in constants.items():
        consumers = [name for name, source in _module_sources()
                     if name != home and any(sp in source for sp in spellings)]
        check(f"{constant} is read outside {home}"
              + (f" (by {consumers})" if consumers else " — NOTHING reads it"),
              bool(consumers))
    # The values themselves must not be copied: an engine spelling out
    # ["per-run", "per-page"] would look correct and drift silently.
    copies = [name for name, source in _module_sources()
              if name != "proxy_pool.py" and '"per-run", "per-page"' in source]
    check("no module re-spells ROTATE_MODES' values instead of importing them"
          + (f" ({copies} does)" if copies else ""), not copies)


def check_dockerfile_copies_what_it_imports():
    print("\n[Dockerfile]")
    path = os.path.join(REPO, "Dockerfile")
    if not os.path.exists(path):
        check("Dockerfile exists", False)
        return
    dockerfile = open(path, encoding="utf-8").read()
    # Join line continuations first: a multi-line COPY is the normal shape
    # here, and reading only its first line would let this check pass while
    # the image was missing everything after the first backslash.
    joined = re.sub(r"\\\s*\n", " ", dockerfile)
    copied = set()
    for line in re.findall(r"^COPY\s+(.+)$", joined, re.MULTILINE):
        for token in line.split():
            if token.endswith(".py"):
                copied.add(token)
    # The entrypoint's transitive local imports — the image should carry
    # exactly these. The entrypoint here is catalog_client.py, not a browser
    # engine: on this site the API path needs no Chromium, so the image does
    # not carry one. Repos in this family have shipped an image that died
    # with ModuleNotFoundError on every invocation because one module was
    # missing from this list, and CI never built it.
    local = {n[:-3] for n, _ in _module_sources()}
    needed, queue = set(), ["catalog_client"]
    while queue:
        module = queue.pop()
        if module in needed:
            continue
        needed.add(module)
        source = open(os.path.join(REPO, f"{module}.py"), encoding="utf-8").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in local:
                queue.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in local:
                        queue.append(alias.name)
    missing = {f"{m}.py" for m in needed} - copied
    check("the Dockerfile COPY list carries every module the entrypoint "
          "imports" + (f" (missing {sorted(missing)})" if missing else ""),
          not missing)
    check("and carries no test suite or fixtures",
          "smoke_test.py" not in copied and "captures" not in dockerfile)
    check("and no .env is baked into the image",
          not re.search(r"^COPY\s+.*\.env(\s|$)", dockerfile, re.MULTILINE))


def check_sample_output():
    print("\n[sample output]")
    columns = list(asdict(Product()).keys())
    json_path = os.path.join(REPO, "sample_output.json")
    csv_path = os.path.join(REPO, "sample_output.csv")
    if not (os.path.exists(json_path) and os.path.exists(csv_path)):
        check("sample_output.json and sample_output.csv are committed", False)
        return
    rows = json.load(open(json_path, encoding="utf-8"))
    check("the sample holds rows from a real run", bool(rows))
    eq("its columns match the Product schema", list(rows[0].keys()), columns)
    with open(csv_path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    eq("the CSV header matches too", header, columns)
    blob = json.dumps(rows).lower()
    for marker in ("sample-listing", "lorem ipsum", "example.com", "your_api_key"):
        check(f"the sample is not fabricated ({marker!r} absent)", marker not in blob)
    check("every sample row names its provenance",
          all(r.get("price_source") for r in rows))


def check_oldest_supported_python_can_parse_it():
    print("\n[Python floor]")
    # pyproject.toml and the CI matrix both claim 3.9. Claiming a floor
    # without testing it is how a walrus operator or an `X | None` annotation
    # ships and breaks it for everyone on that version. This
    # parses every module under 3.9's grammar; CI additionally RUNS the suite
    # on 3.9, which is the half this cannot do.
    for name, source in _module_sources():
        try:
            ast.parse(source, filename=name, feature_version=(3, 9))
            ok, why = True, ""
        except SyntaxError as e:
            ok, why = False, f" — {e}"
        check(f"{name} parses under Python 3.9's grammar{why}", ok)
    for extra in ("smoke_test.py", os.path.join("tests", "test_smoke.py"),
                  os.path.join(".github", "ci_checks.py")):
        path = os.path.join(REPO, extra)
        if not os.path.exists(path):
            continue
        try:
            ast.parse(open(path, encoding="utf-8").read(), filename=extra,
                      feature_version=(3, 9))
            ok = True
        except SyntaxError as e:
            ok = False
            print(f"        {e}")
        check(f"{extra} parses under Python 3.9's grammar", ok)


def check_packaging_matches_the_tree():
    print("\n[packaging]")
    path = os.path.join(REPO, "pyproject.toml")
    if not os.path.exists(path):
        check("pyproject.toml exists", False)
        return
    try:
        import tomllib
    except ImportError:
        check("pyproject.toml cross-check (tomllib needs 3.11 — SKIPPED here, "
              "CI's 3.12 leg runs it)", True)
        return
    with open(path, "rb") as handle:
        config = tomllib.load(handle)
    declared = set(config["tool"]["setuptools"]["py-modules"])
    on_disk = {name[:-3] for name, _ in _module_sources()}
    eq("every module on disk is declared in pyproject", on_disk - declared, set())
    eq("and nothing declared is missing from disk", declared - on_disk, set())
    # requirements.txt and the dependency list are kept in sync BY HAND, and
    # CI installs only requirements.txt — so drift here goes unnoticed there.
    requirements = [line.split("#")[0].strip()
                    for line in open(os.path.join(REPO, "requirements.txt"),
                                     encoding="utf-8")
                    if line.strip() and not line.strip().startswith("#")]
    eq("pyproject's dependencies match requirements.txt",
       sorted(config["project"]["dependencies"]), sorted(requirements))
    for engine in ("playwright", "puppeteer", "selenium"):
        extra = config["project"]["optional-dependencies"][engine]
        pins = [line.split("#")[0].strip()
                for line in open(os.path.join(REPO, f"requirements-{engine}.txt"),
                                 encoding="utf-8")
                if line.strip() and not line.strip().startswith("#")]
        eq(f"the {engine} extra matches requirements-{engine}.txt",
           sorted(extra), sorted(pins))


def check_ci_checks_are_one_implementation():
    print("\n[CI checks]")
    # ONE implementation, invoked from here AND from the workflow. The older
    # repos in this family carried this script plus a narrower inline grep in
    # tests.yml, and the two disagreed: the inline one matched only ws:// and
    # wss://, so an http://user:pass@ credential would have sailed past CI,
    # while the script itself failed on its own main branch.
    path = os.path.join(REPO, ".github", "ci_checks.py")
    if not os.path.exists(path):
        check(".github/ci_checks.py exists", False)
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("ci_checks", path)
    ci_checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci_checks)

    failures = ci_checks.secret_check()
    check("nothing credential-shaped, no session material and no personal data "
          "is committed" + (f" — {failures[:3]}" if failures else ""), not failures)
    failures = ci_checks.sample_check()
    check("the committed sample is real and matches the schema"
          + (f" — {failures[:3]}" if failures else ""), not failures)

    # And the CLI itself, not only the functions underneath it: a signature
    # or a call that drifts inside main() is invisible to a test that only
    # exercises the internals, and CI invokes exactly this command line.
    import subprocess
    result = subprocess.run([sys.executable, path, "--all"],
                            capture_output=True, text=True, cwd=REPO)
    check("`python3 .github/ci_checks.py --all` exits 0"
          + ("" if result.returncode == 0 else
             f" — {(result.stdout + result.stderr).strip().splitlines()[-1][:120]}"),
          result.returncode == 0)

    workflow = os.path.join(REPO, ".github", "workflows", "tests.yml")
    if os.path.exists(workflow):
        text = open(workflow, encoding="utf-8").read()
        check("the workflow CALLS ci_checks.py rather than reimplementing it",
              "ci_checks.py" in text)
        check("and carries no inline credential grep of its own",
              not re.search(r"grep\s+-[a-zA-Z]*\s*['\"]?\(ws\|wss\)", text))
    else:
        check(".github/workflows/tests.yml exists", False)


# ---------------------------------------------------------------------------
# 6. The concurrency machinery, with the browser stubbed out
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Just enough of a `requests` response for the paid paths to run."""

    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status_code = payload, status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def check_credentialled_paths_run():
    print("\n[paths a credential gates]")
    # The paths nobody runs are the paths with no evidence behind them, and in
    # this family they are where the copied core rotted: a key printed to a
    # terminal, a user agent never applied, a documented flag that returns 400.
    # This repo has no 2Captcha key to test with, so the HTTP call is stubbed
    # and everything around it is executed for real — which is what catches a
    # name that does not resolve, a signature that drifted, or a response key
    # that is read but never returned.
    import requests
    import captcha_solver as cs
    import fingerprint_client as fc
    import scraper_api_client as sac

    calls = []
    params = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if url.endswith("/createTask"):
            return _FakeResponse({"errorId": 0, "taskId": "T1"})
        if url.endswith("/getTaskResult"):
            return _FakeResponse({"errorId": 0, "status": "ready",
                                  "solution": {"token": "TOKEN-V2"}})
        if url.endswith("/getBalance"):
            return _FakeResponse({"errorId": 0, "balance": "12.5"})
        if "in.php" in url:
            return _FakeResponse({"status": 1, "request": "ID1"})
        if "tasks/sync" in url:
            return _FakeResponse({"status": 200, "body": PAGE_FIXTURE_HTML},
                                 headers={"x-debug": "cost=0.0005"})
        raise AssertionError(url)

    def fake_get(url, **kwargs):
        calls.append(url)
        params.append(kwargs.get("params") or {})
        if "res.php" in url:
            return _FakeResponse({"status": 1, "request": "TOKEN-V1"})
        if "fingerprint" in url:
            return _FakeResponse({
                "id": "fp1", "country": "de", "userAgent": {"value": "UA/9"},
                "screen": {"width": 1920, "height": 1080}, "locale": "de-DE",
                "timezone": "Europe/Berlin",
                "navigator": {"platform": "Win32", "hardwareConcurrency": 8},
                "webgl": {"vendor": "V", "renderer": "R"}})
        raise AssertionError(url)

    real_post, real_get, real_sleep = requests.post, requests.get, cs.time.sleep
    try:
        requests.post, requests.get, cs.time.sleep = fake_post, fake_get, lambda s: None
        turnstile = cs.CaptchaChallenge(kind="turnstile", sitekey="0xFIXTUREFIXTURE",
                                        page_url="https://www.lg.com/ru/support/email-to-ceo")
        eq("the v2 solver returns its token", cs.solve(turnstile, "k" * 32), "TOKEN-V2")
        eq("the v1 fallback returns its token",
           cs.solve(turnstile, "k" * 32, api_version="v1"), "TOKEN-V1")
        v3 = cs.CaptchaChallenge(kind="recaptcha_v3", sitekey="6L" + "a" * 30,
                                 page_url="https://www.lg.com/ru/x")
        eq("and both speak reCAPTCHA too", cs.solve(v3, "k" * 32), "TOKEN-V2")
        eq("the balance check parses its answer", cs.get_balance("k" * 32), 12.5)
        check("the key never rides in a URL on the v2 path",
              all("key=" not in c for c in calls if "api.2captcha.com" in c))

        fp = fc.get_fingerprint("k" * 32, tags="Windows,Chrome,Desktop", cache_dir=None)
        kwargs = fc.playwright_context_kwargs(fp)
        eq("a fingerprint's user agent is actually applied",
           kwargs.get("user_agent"), "UA/9")
        eq("its locale comes from the response, not from the country",
           kwargs.get("locale"), "de-DE")
        eq("and its timezone is applied at all", kwargs.get("timezone_id"),
           "Europe/Berlin")
        # The defect this pins: every engine in the family shipped
        # --fp-tags "Windows,Chrome,Desktop", and the API answers 400 to a
        # list. A caller that passes one anyway gets it trimmed, and says so.
        sent = [p.get("tags") for p in params if "tags" in p]
        eq("a three-tag --fp-tags is trimmed to the one tag the API accepts",
           sent, ["Windows"])
        # The key DOES ride in this endpoint's query string — that is the
        # API's shape, not a choice — which is why the client wraps the call
        # and redacts before re-raising. Asserted in check_credentials_never_leak.
        check("the fingerprint call is the one that carries its key in the URL",
              any("key" in p for p in params))

        with tempfile.TemporaryDirectory() as tmp:
            class Args:
                url = "https://www.lg.com/ru/televisions"
                key = "k" * 32
                timeout = 60
                cdp_url = None
                wait_text = wait_element = wait_state = None
                pages = 1
                dump_html = None
                out = os.path.join(tmp, "api_run")
                category = "saas"
                format = "json"
                allow_empty = False
                retries = 0
                retry_delay = 0
                delay = 0

            with redirect_stdout(io.StringIO()):
                rc = sac.scrape(Args())
            eq("the browserless engine parses a real response and exits 0", rc, 0)
            rows = json.load(open(f"{os.path.join(tmp, 'api_run')}.json", encoding="utf-8"))
            eq("with the same rows the browser engines produce", len(rows), 2)
    finally:
        requests.post, requests.get, cs.time.sleep = real_post, real_get, real_sleep


def check_concurrency_machinery():
    print("\n[concurrency]")
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        SKIPPED_GROUPS.append("concurrency (playwright not installed)")
        check("concurrency machinery (playwright not installed — SKIPPED)", True)
        return

    # A live run cannot always reach this code: pages 1 and 2 are fetched
    # alone and decide whether the rest may be addressed, so a blocked page 1
    # means the workers never start.
    class _StubSession:
        def __init__(self, *a, **kw):
            self.pool = None

        def open(self):
            return self

        def close(self):
            pass

    class _StubPlaywright:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Args:
        pages = 12
        delay = 0
        out = "stub"
        retries = 1

    original = (engine.sync_playwright, engine._BrowserSession, engine._fetch_one_page)
    fetched = []
    lock = __import__("threading").Lock()
    try:
        engine.sync_playwright = lambda: _StubPlaywright()
        engine._BrowserSession = _StubSession

        def fake_fetch(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            outcome = engine.PageOutcome(page_num=page_num, url=url)
            outcome.state = page_flow.PageState(page_flow.CONTENT, "stub")
            outcome.products = [_row(sku=f"p{page_num}-{i}") for i in range(3)]
            return outcome

        engine._fetch_one_page = fake_fetch
        specs = [(n, f"u{n}") for n in range(3, 13)]
        results, unattempted, ran_out = engine._fetch_pages_concurrently(
            Args(), None, specs, 4)
        eq("every queued page is fetched exactly once",
           sorted(fetched), [n for n, _ in specs])
        eq("nothing is left unattempted when all pages succeed", unattempted, [])
        eq("outcomes are restorable to page order",
           [o.page_num for o in sorted(results, key=lambda o: o.page_num)],
           [n for n, _ in specs])

        # A page with no listings ends the listing and stops dispatch, so
        # asking for 50 pages of a 5-page search costs at most N-1 extra.
        fetched.clear()

        def fetch_until_empty(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            outcome = engine.PageOutcome(page_num=page_num, url=url)
            outcome.state = page_flow.PageState(page_flow.CONTENT, "stub")
            outcome.products = [] if page_num >= 5 else [_row(sku=f"p{page_num}")]
            return outcome

        engine._fetch_one_page = fetch_until_empty
        results, unattempted, ran_out = engine._fetch_pages_concurrently(
            Args(), None, specs, 2)
        check("the end of the listing stops dispatch", ran_out)
        check("so most of the queue is never fetched "
              f"({len(fetched)} fetched of {len(specs)} queued)",
              len(fetched) < len(specs))
        check("and the unattempted pages are REPORTED, not counted as failed",
              sorted(unattempted) == sorted(n for n, _ in specs if n not in fetched))

        # A worker that raises must neither hang the run nor lose its
        # siblings' pages.
        fetched.clear()

        def sometimes_explodes(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            if page_num == 4:
                raise RuntimeError("stub worker failure")
            outcome = engine.PageOutcome(page_num=page_num, url=url)
            outcome.state = page_flow.PageState(page_flow.CONTENT, "stub")
            outcome.products = [_row(sku=f"p{page_num}")]
            return outcome

        engine._fetch_one_page = sometimes_explodes
        results, unattempted, ran_out = engine._fetch_pages_concurrently(
            Args(), None, specs, 3)
        check("a worker that raises does not hang the run", True)
        check("and its siblings' pages still come back", len(results) >= 1)
    finally:
        engine.sync_playwright, engine._BrowserSession, engine._fetch_one_page = original


def check_worker_pools_start_on_different_exits():
    print("\n[worker pools]")
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        check("worker pools (playwright not installed — SKIPPED)", True)
        return
    pool = proxy_pool.ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    starts = [engine._worker_pool(pool, i).current for i in range(3)]
    eq("each worker starts on a different exit", len(set(starts)), 3)
    check("and holds its own pool object, so no thread needs a lock",
          engine._worker_pool(pool, 0) is not engine._worker_pool(pool, 0))
    eq("with no pool there is nothing to hand out",
       engine._worker_pool(None, 0), None)


def _catalog_args(tmp, **kw):
    """The argument object `catalog_client.parse_args()` produces.

    Built by hand rather than through parse_args so a case can set `pages`
    or `retries` without going through argv; the flag set itself is checked
    against the real parser below.
    """
    base = dict(url="https://www.lg.com/ru/televisions", category="televisions",
                pages=3, delay=0.0, retries=2, retry_delay=0.0, timeout=5.0,
                format="json", out=os.path.join(tmp, "out"), proxy=None,
                proxy_file=None, proxy_rotate="per-run", proxy_shuffle=False,
                allow_empty=False, dump_html=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _api_payload(records):
    """A catalogue-API answer carrying exactly `records`, in the real shape."""
    page = json.loads(API_FIXTURE_JSON)
    page["data"][0]["productList"] = records
    return page


def _run_catalog(tmp, pages_html, api_pages, **kw):
    """Run catalog_client.scrape() with both network calls stubbed out.

    `pages_html` is (html, status) for the category page; `api_pages` is a
    list of (payload, status), one per POST, the last entry repeating.
    Returns (exit code, the rows written, the sidecar or None, call log).
    """
    calls = {"api": 0}
    original = (catalog_client.fetch_category_page, catalog_client.fetch_api_page)

    def fake_page(session, url, timeout):
        return pages_html

    def fake_api(session, form, page_num, referer, timeout):
        calls["api"] += 1
        return api_pages[min(calls["api"] - 1, len(api_pages) - 1)]

    args = _catalog_args(tmp, **kw)
    catalog_client.fetch_category_page, catalog_client.fetch_api_page = fake_page, fake_api
    try:
        with redirect_stdout(io.StringIO()):
            rc = catalog_client.scrape(args)
    finally:
        catalog_client.fetch_category_page, catalog_client.fetch_api_page = original

    rows, meta = None, None
    if os.path.exists(f"{args.out}.json"):
        rows = json.load(open(f"{args.out}.json", encoding="utf-8"))
    if os.path.exists(f"{args.out}.meta.json"):
        meta = json.load(open(f"{args.out}.meta.json", encoding="utf-8"))
    return rc, rows, meta, calls


def check_catalog_client_run():
    """The primary engine, driven end to end with the network stubbed.

    Everything below the HTTP calls is the real thing: the form is read off
    the page fixture, the payload is built from it, the pages are classified,
    the rows are parsed and deduped, and finish_run decides the exit code.
    Without this group the engine that actually ships was the only one no
    check ever ran.
    """
    print("\n[the catalogue engine, end to end]")
    served = (PAGE_FIXTURE_HTML, 200)
    full = _api_payload(json.loads(API_FIXTURE_JSON)["data"][0]["productList"])
    empty = _api_payload([])

    with tempfile.TemporaryDirectory() as tmp:
        rc, rows, meta, calls = _run_catalog(tmp, served, [(full, 200), (empty, 200)])
        eq("a run that reaches the end of the catalogue exits 0", rc, 0)
        eq("and writes the products the API returned", len(rows or []), 2)
        eq("the API was called once per page, no more", calls["api"], 2)
        eq("the sidecar calls it complete", (meta or {}).get("status"), "complete")
        eq("the sidecar carries the number of products the category pages "
           "through, not the site's variant count",
           (meta or {}).get("total_results"), 50)
        eq("an empty page ends the run as exhausted, not as an error",
           (meta or {}).get("stop_reason"), "listing_exhausted")
        eq("both pages count as completed", (meta or {}).get("pages_completed"), 2)
        check("every row says which path produced it",
              all(r["price_source"] == "api" for r in rows or [{}]))

    with tempfile.TemporaryDirectory() as tmp:
        # The same products again on page 2 — pagination looping back on
        # itself, which is a property of the DATA, not of a selector.
        rc, rows, meta, calls = _run_catalog(tmp, served, [(full, 200)])
        eq("a page that repeats the previous one ends the run", rc, 0)
        eq("and its duplicates never reach the output", len(rows or []), 2)
        eq("the sidecar says why it stopped",
           (meta or {}).get("stop_reason"), "no_new_products")

    with tempfile.TemporaryDirectory() as tmp:
        rc, rows, meta, calls = _run_catalog(
            tmp, (AKAMAI_REFUSAL_FIXTURE_HTML, 403), [(full, 200)])
        eq("a refusal page is exit 3, not 'zero products'", rc, EXIT_BLOCKED)
        eq("and the catalogue API is never called", calls["api"], 0)
        check("nothing is written", rows is None and meta is None)
        check("but the refusal itself is saved for a human to read",
              os.path.exists(os.path.join(tmp, "out_page1_debug.html")))

    with tempfile.TemporaryDirectory() as tmp:
        rc, rows, meta, calls = _run_catalog(tmp, (SHELL_FIXTURE_HTML, 200),
                                             [(full, 200)])
        eq("a page with no #categoryFilterForm exits 4", rc, EXIT_NO_PRODUCTS)
        eq("and no API call is invented from a hardcoded category id",
           calls["api"], 0)

    with tempfile.TemporaryDirectory() as tmp:
        rc, rows, meta, calls = _run_catalog(
            tmp, served, [(full, 200), (None, 500)], retries=3)
        eq("a run that loses a page mid-way is partial, exit 6", rc, EXIT_PARTIAL)
        eq("the page that was gathered is still written", len(rows or []), 2)
        eq("the sidecar names WHICH page failed", (meta or {}).get("pages_failed"), [2])
        eq("and calls the run partial", (meta or {}).get("status"), "partial")
        eq("the failing page was retried --retries times, then given up on",
           calls["api"], 1 + 3)

    with tempfile.TemporaryDirectory() as tmp:
        # The category page itself never arrived. The family contract maps
        # "nothing gathered, nothing blocking" to exit 4; what keeps that
        # honest is the log line and the absence of a sidecar claiming a
        # successful empty category. Pinned so it cannot drift silently.
        rc, rows, meta, calls = _run_catalog(tmp, (None, None), [(full, 200)])
        eq("a category page that never loaded exits 4", rc, EXIT_NO_PRODUCTS)
        eq("with no API call attempted", calls["api"], 0)
        check("and no sidecar claiming an empty category", meta is None)

    # The flag contract. This engine drives no browser, so it carries the
    # shared flags and none of the browser-only ones — asserted in both
    # directions so neither set drifts.
    argv = sys.argv
    sys.argv = ["catalog_client", "--url", "https://www.lg.com/ru/televisions"]
    try:
        flags = set(vars(catalog_client.parse_args()))
    finally:
        sys.argv = argv
    shared = {"url", "category", "pages", "format", "out", "delay", "retries",
              "retry_delay", "proxy", "proxy_file", "proxy_rotate",
              "proxy_shuffle", "allow_empty", "dump_html"}
    missing = shared - flags
    check("the catalogue engine carries every shared flag"
          + (f" (missing {sorted(missing)})" if missing else ""), not missing)
    browser_only = {"concurrency", "twocaptcha_key", "captcha_api",
                    "solve_captcha", "min_score", "cdp_endpoint", "headless",
                    "fingerprint", "fp_tags", "fp_country",
                    "proxy_block_retries"}
    present = browser_only & flags
    check("and none of the browser-only ones, which would do nothing here"
          + (f" (has {sorted(present)})" if present else ""), not present)
    eq("its extras over the shared set are the timeout it needs",
       sorted(flags - shared), ["timeout"])

    # The locale guard runs in the entry point, not only in the parser.
    sys.argv = ["catalog_client", "--url", "https://www.lg.com/us/tvs"]
    try:
        with redirect_stdout(io.StringIO()):
            catalog_client.parse_args()
        check("a locale this repo cannot scrape is refused before any request", False)
    except SystemExit as e:
        eq("a locale this repo cannot scrape is refused before any request, "
           "as bad usage", e.code, 2)
    finally:
        sys.argv = argv


# ---------------------------------------------------------------------------
# v0.2.0: the four defects the 2026-09-25 audit reproduced live
# ---------------------------------------------------------------------------
def _meta(prefix):
    path = f"{prefix}.meta.json"
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


def _diff(old, new, *extra):
    import diff_runs
    argv = sys.argv
    sys.argv = ["diff_runs", "--old", old, "--new", new, *extra]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = diff_runs.main()
    finally:
        sys.argv = argv
    return rc, buf.getvalue()


def check_a_sample_is_never_a_full_listing():
    """`--pages 2` of a 4-page category wrote status=complete, and diff_runs
    then reported the unread half as 24 delisted models. Measured live."""
    print("\n[a limited run is not a complete listing]")
    rows = [_row(sku=f"S{i}") for i in range(24)]
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "lim")
        with redirect_stdout(io.StringIO()):
            rc = finish_run(rows, prefix, "json", False, blocked=False,
                            stop_reason="completed", pages_requested=2,
                            pages_completed=2, start_url="u", final_url="u",
                            total_results=48, page_count=4)
        meta = _meta(prefix)
        eq("running out of --pages early is still a successful task", rc, 0)
        eq("but the sidecar says limited, not complete", meta["status"], "limited")
        eq("listing_complete is false", meta["listing_complete"], False)
        eq("scope names it a sample", meta["scope"], "limited_pages")
        eq("stop_reason says --pages ran out", meta["stop_reason"], "page_limit")
        eq("and the ratio says how much was read", meta["completeness_ratio"], 0.5)

        with redirect_stdout(io.StringIO()):
            finish_run(rows, prefix, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=3,
                       pages_completed=3, start_url="u", final_url="u")
        eq("with no count from the site at all, a used-up --pages proves "
           "nothing either", _meta(prefix)["status"], "limited")

        with redirect_stdout(io.StringIO()):
            finish_run(rows, prefix, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=2,
                       pages_completed=2, start_url="u", final_url="u",
                       total_results=48, page_count=2)
        meta = _meta(prefix)
        eq("reading every page the site reported IS complete", meta["status"], "complete")
        eq("and the reason becomes the data's, not the loop's",
           meta["stop_reason"], "listing_exhausted")

        with redirect_stdout(io.StringIO()):
            finish_run(rows, prefix, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=2,
                       pages_completed=2, start_url="u", final_url="u",
                       total_results=24)
        eq("so is reaching the site's own product count",
           _meta(prefix)["status"], "complete")

    served = (PAGE_FIXTURE_HTML, 200)
    full = _api_payload(json.loads(API_FIXTURE_JSON)["data"][0]["productList"])
    with tempfile.TemporaryDirectory() as tmp:
        rc, rows_, meta, calls = _run_catalog(tmp, served, [(full, 200)], pages=1)
        eq("the catalogue engine's --pages 1 of a 5-page category exits 0", rc, 0)
        eq("and is written as limited", (meta or {}).get("status"), "limited")
        eq("with the API's page count in the sidecar", (meta or {}).get("page_count"), 5)

    # The live failure, end to end through diff_runs.
    with tempfile.TemporaryDirectory() as tmp:
        full_p, lim_p = os.path.join(tmp, "full"), os.path.join(tmp, "lim")
        url = "https://www.lg.com/ru/televisions"
        with redirect_stdout(io.StringIO()):
            finish_run([_row(sku=f"S{i}") for i in range(48)], full_p, "json",
                       False, blocked=False, stop_reason="listing_exhausted",
                       pages_requested=10, pages_completed=4, start_url=url,
                       final_url=url, total_results=48, page_count=4)
            finish_run(rows, lim_p, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=2,
                       pages_completed=2, start_url=url, final_url=url,
                       total_results=48, page_count=4)
        rc, out = _diff(f"{full_p}.json", f"{lim_p}.json")
        eq("diff_runs refuses a full run against a limited one", rc, 2)
        check("and reports no removals at all", " removed," not in out)
        rc, out = _diff(f"{full_p}.json", f"{lim_p}.json", "--force")
        check("--force still compares, and says what it found",
              rc == 0 and "24 removed" in out)
        rc, _ = _diff(f"{full_p}.json", f"{full_p}.json")
        eq("two complete runs of one listing diff normally", rc, 0)

        os.remove(f"{lim_p}.meta.json")
        rc, out = _diff(f"{full_p}.json", f"{lim_p}.json")
        eq("a run with no sidecar is refused, not trusted", rc, 2)

        other = os.path.join(tmp, "fridges")
        with redirect_stdout(io.StringIO()):
            finish_run([_row(sku="F1")], other, "json", False, blocked=False,
                       stop_reason="listing_exhausted", pages_requested=1,
                       pages_completed=1,
                       start_url="https://www.lg.com/ru/refrigerators",
                       final_url="u", total_results=1, page_count=1)
        rc, out = _diff(f"{full_p}.json", f"{other}.json")
        eq("two complete runs of DIFFERENT listings are refused", rc, 2)
        eq("while ?page=1 is the same listing as no parameter",
           __import__("diff_runs")._listing_key(url + "?page=1"),
           __import__("diff_runs")._listing_key(url + "/"))


def check_only_lg_is_fetched():
    """`endswith("lg.com")` accepted notlg.com, and the form's own absolute
    action was POSTed to wherever it pointed."""
    print("\n[only https://…lg.com is fetched or POSTed to]")
    refused = ("https://notlg.com/ru/televisions",
               "https://evillg.com/ru/televisions",
               "https://lg.com.attacker.tld/ru/televisions",
               "http://www.lg.com/ru/televisions",
               "file:///ru/televisions",
               "www.lg.com/ru/televisions",
               "https://user@www.lg.com/ru/televisions",
               "https://www.lg.com:8443/ru/televisions",
               "https://www.xn--l-3ga.com/ru/televisions")   # IDN lookalike
    for url in refused:
        check(f"refused: {url}",
              product_parser.unsupported_locale_reason(url) is not None)
    for url in ("https://www.lg.com/ru/televisions", "https://lg.com/ua/televisions",
                "https://WWW.LG.COM:443/ru/televisions"):
        eq(f"accepted: {url}", product_parser.unsupported_locale_reason(url), None)
    check("no scheme says so, rather than calling the host a locale",
          "no scheme" in product_parser.unsupported_locale_reason(
              "www.lg.com/ru/televisions"))

    page = "https://www.lg.com/ru/televisions"
    reason = product_parser.api_endpoint_reason
    eq("the site's own action is allowed",
       reason("https://www.lg.com/ru/mkt/ajax/category/retrieveCategoryProductList", page),
       None)
    for bad in ("http://169.254.169.254/latest/meta-data",
                "https://evil.example/ru/mkt/ajax/x",
                "https://shop.lg.com/ru/mkt/ajax/x",
                "https://www.lg.com/ru/admin/delete",
                "https://www.lg.com/ua/mkt/ajax/x"):
        check(f"an action pointing at {bad} is refused", reason(bad, page) is not None)

    evil = PAGE_FIXTURE_HTML.replace(
        'action="/ru/mkt/ajax/category/retrieveCategoryProductList"',
        'action="http://169.254.169.254/latest/meta-data"').replace(
        'data-price-sync-url="/ru/mkt/ajax/priceSync/retrievePlpPriceSyncList"',
        'data-price-sync-url="//evil.example/p"')
    check("the fixture really was rewritten", "169.254" in evil and "evil.example" in evil)
    form = product_parser.parse_catalog_form(evil, page)
    eq("an off-origin price endpoint is dropped", form["price_sync_url"], None)
    full = _api_payload(json.loads(API_FIXTURE_JSON)["data"][0]["productList"])
    with tempfile.TemporaryDirectory() as tmp:
        rc, rows, meta, calls = _run_catalog(tmp, (evil, 200), [(full, 200)])
        eq("a page whose form points off-site exits 4", rc, EXIT_NO_PRODUCTS)
        eq("and nothing is POSTed anywhere", calls["api"], 0)

    class _Resp:
        def __init__(self, status, location=None, text="<html></html>"):
            self.status_code, self.text = status, text
            self.headers = {"Location": location} if location else {}
            self.is_redirect = location is not None

    class _Session:
        def __init__(self, answers):
            self.answers, self.urls = list(answers), []

        def get(self, url, timeout=None, allow_redirects=True):
            self.urls.append((url, allow_redirects))
            return self.answers.pop(0)

    s = _Session([_Resp(302, "https://evil.example/ru/televisions")])
    eq("a redirect off lg.com is not followed",
       catalog_client.fetch_category_page(s, page, 5), (None, None))
    eq("the redirect target is never requested", len(s.urls), 1)
    check("and requests' own redirect-following is off",
          all(not follow for _, follow in s.urls))
    s = _Session([_Resp(301, "/ru/televisions/"), _Resp(200, text="ok")])
    eq("a redirect within lg.com is followed",
       catalog_client.fetch_category_page(s, page, 5), ("ok", 200))
    eq("to the resolved address", s.urls[-1][0], "https://www.lg.com/ru/televisions/")
    s = _Session([_Resp(302, "/ru/loop")] * 10)
    eq("a redirect loop gives up", catalog_client.fetch_category_page(s, page, 5),
       (None, None))
    src = inspect.getsource(catalog_client.fetch_api_page)
    check("the API POST does not follow redirects", "allow_redirects=False" in src)


def check_output_is_written_atomically():
    """A missing --out directory crashed after every page was fetched, and a
    run killed mid-write left a torn file where last night's good one was."""
    print("\n[output is written atomically]")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "not", "yet", "there", "tv")
        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row(sku="a")], prefix, "both", False, blocked=False,
                            stop_reason="listing_exhausted", pages_requested=1,
                            pages_completed=1, start_url="u", final_url="u")
        eq("a missing parent directory is created", rc, 0)
        check("and all three files land in it",
              all(os.path.exists(prefix + ext) for ext in (".json", ".csv", ".meta.json")))

        good = [{"sku": "yesterday"}]
        with open(f"{prefix}.json", "w", encoding="utf-8") as f:
            json.dump(good, f)
        original = output_writer.json.dump

        def dies_half_way(obj, f, **kw):
            f.write('[{"sku": "tod')
            raise KeyboardInterrupt
        output_writer.json.dump = dies_half_way
        try:
            output_writer.write_json([_row(sku="today")], f"{prefix}.json")
            check("an interrupted write re-raises", False)
        except KeyboardInterrupt:
            check("an interrupted write re-raises", True)
        finally:
            output_writer.json.dump = original
        eq("and leaves the previous file whole",
           json.load(open(f"{prefix}.json", encoding="utf-8")), good)
        leftovers = [n for n in os.listdir(os.path.dirname(prefix)) if n.startswith(".tmp-")]
        eq("with no temporary file left behind", leftovers, [])

        # The sidecar goes first and comes back last: a run that dies
        # between data and sidecar leaves NO sidecar, never an old one.
        original_meta = output_writer.write_run_meta

        def dies(*a, **kw):
            raise KeyboardInterrupt
        output_writer.write_run_meta = dies
        try:
            with redirect_stdout(io.StringIO()):
                finish_run([_row(sku="b")], prefix, "json", False, blocked=False,
                           stop_reason="listing_exhausted", pages_requested=1,
                           pages_completed=1, start_url="u", final_url="u")
        except KeyboardInterrupt:
            pass
        finally:
            output_writer.write_run_meta = original_meta
        check("a run killed before its sidecar leaves no stale one vouching "
              "for the new data", not os.path.exists(f"{prefix}.meta.json"))


def check_numeric_flags_are_validated():
    """`--pages 0` finished as an empty run, `--delay -1` crashed in sleep()
    after page 1, `--retries 0` never made a request."""
    print("\n[numeric flags are checked before any request]")
    import scraper_api_client
    modules = {"catalog_client": catalog_client,
               "scraper_api_client": scraper_api_client, **ENGINES}
    bad = (["--pages", "0"], ["--pages", "-3"], ["--pages", "100000"],
           ["--delay", "-1"], ["--retry-delay", "nan"], ["--delay", "inf"],
           ["--pages", "two"])
    argv = sys.argv
    for name, module in modules.items():
        for extra in bad:
            sys.argv = [name, "--url", "https://www.lg.com/ru/televisions", *extra]
            try:
                with redirect_stdout(io.StringIO()), \
                        __import__("contextlib").redirect_stderr(io.StringIO()):
                    module.parse_args()
                check(f"{name} refuses {' '.join(extra)}", False)
            except SystemExit as e:
                eq(f"{name} refuses {' '.join(extra)} as bad usage", e.code, 2)
            finally:
                sys.argv = argv
    for name, extra in (("catalog_client", ["--retries", "0"]),
                        ("catalog_client", ["--timeout", "0"])):
        sys.argv = [name, "--url", "https://www.lg.com/ru/televisions", *extra]
        try:
            with __import__("contextlib").redirect_stderr(io.StringIO()):
                catalog_client.parse_args()
            check(f"{name} refuses {' '.join(extra)}", False)
        except SystemExit as e:
            eq(f"{name} refuses {' '.join(extra)} as bad usage", e.code, 2)
        finally:
            sys.argv = argv
    sys.argv = ["scraper_api_client", "--url", "https://www.lg.com/ru/televisions",
                "--retries", "0"]
    try:
        with redirect_stdout(io.StringIO()):
            eq("the Scraper API's --retries counts EXTRA attempts, so 0 is valid",
               scraper_api_client.parse_args().retries, 0)
    except SystemExit:
        check("the Scraper API's --retries counts EXTRA attempts, so 0 is valid", False)
    finally:
        sys.argv = argv
    sys.argv = ["catalog_client", "--url", "https://www.lg.com/ru/televisions",
                "--pages", "4", "--delay", "0"]
    try:
        args = catalog_client.parse_args()
        eq("valid values still parse", (args.pages, args.delay), (4, 0.0))
    finally:
        sys.argv = argv


def check_env_report_hides_every_credential():
    """`python3 env_config.py` printed any value without an "@" — which a
    CDP endpoint carrying `?token=` does not have."""
    print("\n[the .env report hides every credential]")
    import subprocess
    secret = "synthetictoken" + "Q" * 18
    env = {k: v for k, v in os.environ.items() if k not in env_config.ENV_KEYS}
    env.update(LG_CDP_ENDPOINT=f"wss://cdp.example/?token={secret}",
               LG_PROXY=f"http://10.0.0.1:3128/?key={secret}",
               LG_URL="https://www.lg.com/ru/televisions",
               PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run([sys.executable, os.path.join(REPO, "env_config.py")],
                             cwd=tmp, env=env, capture_output=True, text=True,
                             timeout=60)
    text = out.stdout + out.stderr
    check("a token in a query string is not printed", secret not in text)
    check("the category URL still is", "https://www.lg.com/ru/televisions" in text)



def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    print("lg-scraper offline suite")
    print("=" * 62)
    for group in (check_parser_values, check_api_path_and_agreement,
                  check_no_price_is_invented, check_pagination_convention,
                  check_supported_locales, check_page_states,
                  check_known_limitations_are_pinned,
                  check_shortfall_arithmetic,
                  check_fingerprint_kwargs_are_ones_playwright_accepts,
                  check_captcha_detection, check_credentials_never_leak,
                  check_remote_connect_failures_are_redacted,
                  check_proxy_rotation, check_proxy_preflight,
                  check_output_contract, check_catalog_client_run,
                  check_a_sample_is_never_a_full_listing,
                  check_only_lg_is_fetched,
                  check_output_is_written_atomically,
                  check_numeric_flags_are_validated,
                  check_env_report_hides_every_credential,
                  check_engine_parity,
                  check_readiness_wait_is_csp_safe,
                  check_engine_imports_driver_at_module_level,
                  check_no_undefined_names, check_shared_calls_bind,
                  check_banned_wording, check_removed_flags_stay_removed,
                  check_env_example_matches_code, check_env_precedence,
                  check_policy_constants_have_consumers,
                  check_dockerfile_copies_what_it_imports, check_sample_output,
                  check_ci_checks_are_one_implementation,
                  check_oldest_supported_python_can_parse_it,
                  check_packaging_matches_the_tree,
                  check_credentialled_paths_run,
                  check_concurrency_machinery,
                  check_worker_pools_start_on_different_exits):
        group()

    print("\n" + "=" * 62)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if SKIPPED_GROUPS:
        # Printed in a shape CI greps for: "skipped, engine absent" reads
        # identically to a real import error, so the engine-smoke job fails
        # when this line appears in a run where every engine IS installed.
        print(f"{len(SKIPPED_GROUPS)} group(s) of checks SKIPPED: "
              + ", ".join(SKIPPED_GROUPS))
    for line in FAILED:
        print(f"  FAILED: {line}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
