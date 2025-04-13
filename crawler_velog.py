import argparse
import json
import re
import time
import os
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tqdm import tqdm

# Selenium 관련
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def init_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--enable-unsafe-swiftshader")
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    service = Service(ChromeDriverManager().install(), log_path=os.devnull)
    return webdriver.Chrome(service=service, options=options)

def crawl_velog_urls_selenium(query: str, max_pages: int, max_articles: int) -> list:
    """
    Selenium으로 Velog 검색 결과에서 (URL, date) 튜플을 수집합니다.
    - href가 "/@..." 패턴인 a 태그만 골라내고,
    - 그 a 태그 부모에서 class="subinfo"인 div 안의 span에서 날짜 추출
    """
    collected = []
    seen = set()
    driver = init_driver()
    driver.get(f"https://velog.io/search?q={quote(query)}")

    for _ in range(max_pages):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"^/@")):
            href = a["href"]
            full_url = urljoin("https://velog.io", href)
            if full_url in seen:
                continue

            # 날짜 추출: a 태그 부모 요소에서 class="subinfo"인 div 안의 span
            date = ""
            parent = a.parent
            subinfo = parent.find("div", class_="subinfo")
            if subinfo:
                span = subinfo.find("span")
                if span:
                    date = span.get_text(strip=True)

            seen.add(full_url)
            collected.append((full_url, date))
            if len(collected) >= max_articles:
                break
        if len(collected) >= max_articles:
            break

    driver.quit()
    logger.info(f"총 {len(collected)}개의 (URL, date) 수집 완료")
    return collected

def fetch_velog_content(url: str) -> (str, str):
    """
    Velog 포스트 페이지에서
    - 제목: meta[property="og:title"]
    - 본문: class명에 "atom-one"이 포함된 div
    를 추출하여 반환합니다.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"[{url}] 요청 실패: {e}")
        return "", ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # 제목
    title = ""
    meta_t = soup.find("meta", property="og:title")
    if meta_t and meta_t.get("content"):
        title = meta_t["content"].strip()

    # 본문: class명에 atom-one 포함된 div
    content = ""
    post_div = soup.find("div", class_=lambda c: c and "atom-one" in c)
    if post_div:
        content = post_div.get_text("\n", strip=True)

    return title, content

def crawl_velog_search(args) -> list:
    items = crawl_velog_urls_selenium(args.query, args.max_pages, args.max_articles)
    results = []
    for url, date in tqdm(items, desc="Velog 글 크롤링"):
        title, content = fetch_velog_content(url)
        results.append({
            "url": url,
            "date": date,
            "title": title,
            "content": content
        })
        time.sleep(1)
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Velog 크롤러 (날짜+atom-one 본문)")
    parser.add_argument("--query", type=str, default="IT면접", help="검색 키워드")
    parser.add_argument("--max-pages", type=int, default=10, help="스크롤 반복 횟수")
    parser.add_argument("--max-articles", type=int, default=100, help="최대 수집 글 개수")
    parser.add_argument("--output-path", type=str, default="velog_results.json", help="결과 저장 경로")
    args = parser.parse_args()

    logger.info("Velog 크롤링 시작")
    data = crawl_velog_search(args)
    with open(args.output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"결과가 {args.output_path}에 저장되었습니다.")
