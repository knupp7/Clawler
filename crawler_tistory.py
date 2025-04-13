import argparse
import json
import os
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tqdm import tqdm
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy

# Selenium 관련 모듈
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Trafilatura 설정 (여기서는 주로 BeautifulSoup 사용)
TRAFILATURA_CONFIG = deepcopy(DEFAULT_CONFIG)
TRAFILATURA_CONFIG["DEFAULT"]["DOWNLOAD_TIMEOUT"] = "5"
TRAFILATURA_CONFIG["DEFAULT"]["MAX_REDIRECTS"] = "0"
TRAFILATURA_CONFIG["DEFAULT"]["MIN_OUTPUT_SIZE"] = "50"

def init_driver():
    """Selenium Chrome WebDriver 초기화 (헤드리스 모드)"""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    service_obj = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service_obj, options=options)
    return driver

def scroll_page(driver, pause_time=2):
    """Selenium 드라이버로 페이지를 끝까지 스크롤하여 lazy-load 콘텐츠 확보"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def save_html_page(html: str, filename: str):
    """디버그용: HTML 전체를 파일로 저장"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML 페이지가 {os.path.abspath(filename)} 에 저장되었습니다.")

def crawl_tistory_urls_selenium(query: str, max_pages: int, max_articles: int) -> list:
    """
    Selenium을 사용하여 티스토리 검색 결과 페이지에서 게시글 URL을 수집합니다.
    - 검색 URL 형식:
      https://www.tistory.com/search?keyword={encoded_query}&type=post&sort=ACCURACY&page={page}
    - 페이지 내 게시글 링크는 DOM 내 "div.item_group a.link_cont.zoom_cont" 선택자로 추출합니다.
    - 모든 href가 절대경로라고 가정합니다.
    - 결과가 없는 페이지의 경우 HTML을 저장해 확인할 수 있습니다.
    """
    collected_urls = []
    encoded_query = quote(query)
    base_url = f"https://www.tistory.com/search?keyword={encoded_query}&type=post&sort=ACCURACY&page="
    driver = init_driver()
    
    for page in range(1, max_pages + 1):
        search_url = base_url + str(page)
        driver.get(search_url)
        # 페이지 로딩 및 자바스크립트 실행 대기
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.item_group"))
            )
        except Exception as e:
            logger.error(f"페이지 {page} 로딩 대기 실패: {e}")
            continue
        
        time.sleep(2)  # 자바스크립트 실행 완료 대기
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        
        posts = soup.select("div.item_group a.link_cont.zoom_cont")
        logger.info(f"페이지 {page}: {len(posts)}개의 링크 발견")
        
        if not posts:
            filename = f"tistory_page_{page}.html"
            save_html_page(html, filename)
            logger.info(f"페이지 {page}에서 결과를 찾지 못했습니다. 저장된 HTML 파일을 확인하세요.")
            continue
        
        for post in posts:
            if len(collected_urls) >= max_articles:
                break
            url = post.get("href")
            if url and url.startswith("http") and url not in collected_urls:
                collected_urls.append(url)
        logger.info(f"페이지 {page}: 현재까지 {len(collected_urls)}개의 URL 수집됨")
        if len(collected_urls) >= max_articles:
            break
        time.sleep(1)
    
    driver.quit()
    return collected_urls

def fetch_tistory_content_selenium(url: str) -> (str, str, str):
    """
    Selenium을 사용하여 티스토리 게시글 페이지의 최종 렌더링된 DOM에서 
    제목, 본문, 작성일을 추출합니다. 페이지를 끝까지 스크롤하여 lazy-load된
    콘텐츠까지 확보합니다.
    """
    logger.info(f"Selenium fallback: {url}")
    driver = init_driver()
    driver.get(url)
    time.sleep(3)  # 초기 로딩 대기
    scroll_page(driver, pause_time=2)  # 페이지 하단까지 스크롤
    html = driver.page_source
    driver.quit()
    
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    else:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    
    # 다양한 선택자로 본문을 추출:
    content = ""
    selectors = [
        ("article", None),
        ("div", {"class": "post-content"}),
        ("div", {"id": "post"}),
        ("div", {"id": "content"}),
        ("div", {"class": "entry-content"}),
        ("div", {"class": "postArea"})
    ]
    for tag, attrs in selectors:
        element = soup.find(tag, attrs=attrs)
        if element:
            content = element.get_text(separator="\n", strip=True)
            if len(content.strip()) > 20:
                break

    date_text = ""
    if soup.find("meta", property="article:published_time"):
        date_text = soup.find("meta", property="article:published_time").get("content", "").strip()
    elif soup.find("time"):
        time_tag = soup.find("time")
        date_text = time_tag.get("datetime") or time_tag.get_text(strip=True)
    
    return title, content, date_text

def fetch_tistory_content(url: str) -> (str, str, str):
    """
    티스토리 게시글 페이지에서 제목, 본문, 작성일을 추출합니다.
    기본적으로 requests를 사용하고, 실패 시 Selenium fallback을 사용합니다.
    (본문 내용이 부족하더라도 fallback 없이 현재 결과를 그대로 반환합니다.)
    """
    headers = {
         "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/105.0.0.0 Safari/537.36"),
         "Referer": "https://www.tistory.com/"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"티스토리 페이지 요청 실패: {url} - {e}")
        return fetch_tistory_content_selenium(url)
    
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    
    # 제목 추출
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    else:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    
    # 다양한 선택자로 본문 추출
    content = ""
    if soup.find("article"):
        content = soup.find("article").get_text(separator="\n", strip=True)
    elif soup.find("div", class_="post-content"):
        content = soup.find("div", class_="post-content").get_text(separator="\n", strip=True)
    elif soup.find("div", id="post"):
        content = soup.find("div", id="post").get_text(separator="\n", strip=True)
    elif soup.find("div", id="content"):
        content = soup.find("div", id="content").get_text(separator="\n", strip=True)
    elif soup.find("div", class_="entry-content"):
        content = soup.find("div", class_="entry-content").get_text(separator="\n", strip=True)
    elif soup.find("div", class_="postArea"):
        content = soup.find("div", class_="postArea").get_text(separator="\n", strip=True)
    
    # 작성일 추출
    date_text = ""
    if soup.find("meta", property="article:published_time"):
        date_text = soup.find("meta", property="article:published_time").get("content", "").strip()
    elif soup.find("time"):
        time_tag = soup.find("time")
        date_text = time_tag.get("datetime") or time_tag.get_text(strip=True)
    
    # 본문 내용이 부족하더라도 fallback 없이 그대로 반환함.
    return title, content, date_text


def crawl_tistory_search(args) -> list:
    """
    Selenium을 사용해 티스토리 검색 결과 페이지에서 게시글 URL을 수집한 후,
    각 게시글 페이지에서 제목, 본문, 작성일을 추출하여 결과 리스트를 반환합니다.
    """
    collected_urls = crawl_tistory_urls_selenium(args.query, args.max_pages, args.max_articles)
    logger.info(f"Selenium으로 총 {len(collected_urls)}개의 URL 수집 완료")
    
    results = []
    for url in tqdm(collected_urls, desc="티스토리 글 크롤링"):
        t, c, d = fetch_tistory_content(url)
        if t or c:
            results.append({
                "url": url,
                "title": t,
                "content": c,
                "date": d
            })
        time.sleep(1)
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="티스토리 블로그에서 IT 면접 관련 글 크롤링 (Selenium fallback 사용)"
    )
    parser.add_argument("--query", type=str, default="IT면접", help="검색할 키워드")
    parser.add_argument("--max-pages", type=int, default=10, help="탐색할 최대 페이지 수")
    parser.add_argument("--max-articles", type=int, default=200, help="최대 수집 글 개수")
    parser.add_argument("--output-path", type=str, default="tistory_results.json", help="결과 저장 JSON 파일 경로")
    
    args = parser.parse_args()
    results = crawl_tistory_search(args)
    
    with open(args.output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    
    logger.info(f"티스토리 크롤링 결과가 {args.output_path}에 저장되었습니다.")
