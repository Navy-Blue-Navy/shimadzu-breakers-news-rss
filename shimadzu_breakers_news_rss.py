import os
import re
import sys
import hashlib
import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime


NEWS_URL = "https://www.shimadzu.co.jp/r-breakers/news.html"

OUTPUT_FILE = "shimadzu_breakers_news.xml"

FEED_TITLE = "SHIMADZU Breakers NEWS"
FEED_LINK = NEWS_URL
FEED_DESCRIPTION = "島津製作所ラグビー部 SHIMADZU Breakers ニュースRSS"

REQUEST_TIMEOUT = 30

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    )
}


def rss_date(dt):
    return format_datetime(dt)


def clean_text(text):
    return " ".join(text.split())


def make_guid(article):
    source = (
        article["date_key"]
        + "|"
        + article["title"]
    )

    return hashlib.sha256(
        source.encode("utf-8")
    ).hexdigest()


def extract_date(date_text, title_text):
    """
    news_date:
        2026.10
        2026.9

    news_title:
        10月10日（土）...
        9月26日（土）...

    から年月日を作る。
    """

    year_month = re.search(
        r"(\d{4})\.(\d{1,2})",
        date_text,
    )

    if not year_month:
        raise ValueError(
            f"年月を解析できません: {date_text}"
        )

    year = int(year_month.group(1))
    month = int(year_month.group(2))

    month_day = re.search(
        r"(\d{1,2})月\s*(\d{1,2})日",
        title_text,
    )

    if month_day:
        article_month = int(
            month_day.group(1)
        )
        day = int(
            month_day.group(2)
        )

        # 本文側の月を優先
        month = article_month

    else:
        # 日が取れない場合は月初扱い
        day = 1

    return datetime(
        year,
        month,
        day,
        0,
        0,
        0,
        tzinfo=JST,
    )


def fetch_articles():
    print(f"GET {NEWS_URL}")

    response = requests.get(
        NEWS_URL,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    print(
        f"HTTP: {response.status_code} / "
        f"{len(response.content)} bytes"
    )

    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    news_items = soup.select(
        "div.news_item"
    )

    print(
        f"news_item: {len(news_items)}"
    )

    if not news_items:
        raise RuntimeError(
            "div.news_item が1件も見つかりません"
        )

    articles = {}

    for news_item in news_items:
        date_element = news_item.select_one(
            ".news_date"
        )

        title_element = news_item.select_one(
            ".news_title"
        )

        if not date_element or not title_element:
            continue

        date_text = clean_text(
            date_element.get_text(
                " ",
                strip=True,
            )
        )

        full_title = clean_text(
            title_element.get_text(
                " ",
                strip=True,
            )
        )

        if not date_text or not full_title:
            continue

        try:
            published = extract_date(
                date_text,
                full_title,
            )

        except Exception as e:
            print(
                f"日付解析失敗: {full_title}"
            )
            print(f"  {e}")
            continue

        # RSSタイトルはnews_title全体を使用
        title = full_title

        date_key = published.strftime(
            "%Y-%m-%d"
        )

        # 個別URLがないため、
        # URL + 日付 + ハッシュを疑似的な一意URLにする
        hash_part = hashlib.sha256(
            (
                date_key
                + "|"
                + title
            ).encode("utf-8")
        ).hexdigest()[:12]

        item_url = (
            NEWS_URL
            + "#rss-"
            + date_key
            + "-"
            + hash_part
        )

        # 記事本文もdescription用に取得
        body_text = clean_text(
            news_item.get_text(
                " ",
                strip=True,
            )
        )

        article = {
            "title": title,
            "url": item_url,
            "published": published,
            "date_key": date_key,
            "description": body_text,
        }

        articles[item_url] = article

    if not articles:
        raise RuntimeError(
            "ニュース記事を1件も解析できませんでした"
        )

    print(
        f"取得記事数: {len(articles)}"
    )

    return articles


def load_existing_feed():
    articles = {}

    if not os.path.exists(OUTPUT_FILE):
        print(
            "既存RSSなし：新規作成します"
        )
        return articles

    try:
        tree = ET.parse(
            OUTPUT_FILE
        )

        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            title = item.findtext(
                "title",
                default="",
            ).strip()

            link = item.findtext(
                "link",
                default="",
            ).strip()

            pub_date = item.findtext(
                "pubDate",
                default="",
            ).strip()

            description = item.findtext(
                "description",
                default="",
            ).strip()

            if not link:
                continue

            try:
                published = (
                    parsedate_to_datetime(
                        pub_date
                    )
                )

            except Exception:
                continue

            if published.tzinfo is None:
                published = (
                    published.replace(
                        tzinfo=JST
                    )
                )

            articles[link] = {
                "title": title,
                "url": link,
                "published": published,
                "date_key": (
                    published
                    .astimezone(JST)
                    .strftime("%Y-%m-%d")
                ),
                "description": description,
            }

        print(
            f"既存RSS読込件数: "
            f"{len(articles)}"
        )

    except Exception as e:
        print(
            f"既存RSSの読込に失敗しました: {e}"
        )
        print(
            "安全のため処理を中止します。"
        )
        sys.exit(1)

    return articles


def build_rss(articles):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
        },
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = FEED_TITLE

    ET.SubElement(
        channel,
        "link",
    ).text = FEED_LINK

    ET.SubElement(
        channel,
        "description",
    ).text = FEED_DESCRIPTION

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = rss_date(
        datetime.now(JST)
    )

    sorted_articles = sorted(
        articles.values(),
        key=lambda x: x["published"],
        reverse=True,
    )

    for article in sorted_articles:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = article["title"]

        ET.SubElement(
            item,
            "link",
        ).text = article["url"]

        guid = ET.SubElement(
            item,
            "guid",
            {
                "isPermaLink": "false"
            },
        )

        guid.text = make_guid(
            article
        )

        ET.SubElement(
            item,
            "pubDate",
        ).text = rss_date(
            article["published"]
        )

        ET.SubElement(
            item,
            "description",
        ).text = article[
            "description"
        ]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    print()
    print(
        f"保存完了: {OUTPUT_FILE} "
        f"({len(sorted_articles)}件)"
    )


def main():
    print(
        "SHIMADZU Breakers NEWS RSS生成"
    )
    print("=" * 50)

    existing_articles = (
        load_existing_feed()
    )

    try:
        current_articles = (
            fetch_articles()
        )

    except Exception as e:
        print()
        print(
            "ニュースページの取得・解析に"
            "失敗しました。"
        )
        print(f"エラー: {e}")
        print()
        print(
            "既存RSSは変更せず、"
            "そのまま残します。"
        )
        sys.exit(1)

    new_urls = [
        url
        for url in current_articles
        if url not in existing_articles
    ]

    combined_articles = dict(
        existing_articles
    )

    for url, article in (
        current_articles.items()
    ):
        combined_articles[url] = article

    print()
    print(
        f"今回取得した記事: "
        f"{len(current_articles)}"
    )

    print(
        f"今回初めて確認した記事: "
        f"{len(new_urls)}"
    )

    print(
        f"累積RSS件数: "
        f"{len(combined_articles)}"
    )

    sorted_current = sorted(
        current_articles.values(),
        key=lambda x: x["published"],
        reverse=True,
    )

    print()
    print(
        "今回取得した記事"
    )
    print("-" * 50)

    for article in sorted_current:
        print(
            f"{article['date_key']} / "
            f"{article['title']}"
        )

    build_rss(
        combined_articles
    )


if __name__ == "__main__":
    main()