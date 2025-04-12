import argparse
import json
import re
import time
from urllib.parse import quote
import sys

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tqdm import tqdm
from trafilatura import extract, fetch_url
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy

logger.remove()  # 기본 핸들러 제거
logger.add(sys.stderr, level="DEBUG")

# Trafilatura 설정 (다운로드 타임아웃 등)
TRAFILATURA_CONFIG = deepcopy(DEFAULT_CONFIG)
TRAFILATURA_CONFIG["DEFAULT"]["DOWNLOAD_TIMEOUT"] = "5"
TRAFILATURA_CONFIG["DEFAULT"]["MAX_REDIRECTS"] = "0"
TRAFILATURA_CONFIG["DEFAULT"]["MIN_OUTPUT_SIZE"] = "50"


def fetch_blog_content(url: str) -> (str, str, str):
    try:
        # PC용 URL을 모바일 URL로 변환
        mobile_url = url.replace("://blog.naver.com/", "://m.blog.naver.com/")
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(mobile_url, headers=headers, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"블로그 페이지 요청 실패: {url} - {e}")
        return "", "", ""
    
    html = resp.text
    
    # 디버그: 실제 HTML 내용의 일부를 로그로 남겨 확인하기
    logger.debug(f"크롤링한 HTML 일부: {html[:300]}")
    
    soup = BeautifulSoup(html, "html.parser")
    
    # 제목 추출: og:title 메타태그 우선, 없으면 <h3> 태그 사용
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    else:
        title_tag = soup.find("h3")
        if title_tag:
            title = title_tag.get_text(strip=True)
    
    # 본문 추출: 새 에디터인 경우 (.se-main-container), 없으면 구 에디터 (#postViewArea)
    content = ""
    main_container = soup.find("div", class_="se-main-container")
    if main_container:
        content = main_container.get_text(separator="\n", strip=True)
    else:
        post_view = soup.find("div", id="postViewArea")
        if post_view:
            content = post_view.get_text(separator="\n", strip=True)
    
    # 작성일 추출: 메타태그나 <span> 태그에서 날짜 형식(YYYY.MM.DD) 찾기
    date_text = ""
    og_date = soup.find("meta", property="article:published_time")
    if og_date and og_date.get("content"):
        date_text = og_date["content"].strip()
    else:
        publish_span = soup.find("span", class_="se_publishDate")
        if publish_span:
            date_candidate = publish_span.get_text(strip=True)
            match = re.search(r"\d{4}\.\d{2}\.\d{2}", date_candidate)
            if match:
                date_text = match.group(0)
    
    return title, content, date_text


def crawl_blog_search(args) -> list:
    """
    네이버 블로그 검색 결과를 크롤링합니다.
    - 네이버 블로그 검색 URL: https://search.naver.com/search.naver?where=post&query=<검색어>
    - 검색 결과 페이지에서 블로그 글 링크를 추출합니다.
    - 최대 수집 글 개수 (--max-articles) 한도 내에서 진행합니다.
    """
    collected_urls = []
    results = []
    
    # 검색 URL 구성
    encoded_query = quote(args.query)
    search_url = f"https://search.naver.com/search.naver?where=post&query={encoded_query}"
    
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(search_url, headers=headers, timeout=5)
        resp.raise_for_status()
        logger.debug(f"검색 결과 HTML 일부: {resp.text[:300]}")
    except Exception as e:
        logger.error(f"검색 결과 요청 실패: {search_url} - {e}")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    # 네이버 블로그 검색 결과 HTML 구조:
    # li 안에 view_wrap 안에 api_save_group _keep_wrap 안에 <a> 태그가 있음
    posts = soup.select("li .view_wrap .api_save_group._keep_wrap a")
    
    # 최대 수집 글 개수만큼 URL 수집
    for post in posts:
        if len(collected_urls) >= args.max_articles:
            break
        # data-url 속성에 실제 링크가 있음. 없으면 href로 대체.
        url = post.get("data-url") or post.get("href")
        if not url or url.strip() in {"#", "javascript:void(0)"} or not url.startswith("http"):
            continue
        if url not in collected_urls:
            collected_urls.append(url)
    
    # 각 블로그 글 URL에 대해 본문, 제목, 작성일 추출
    for url in tqdm(collected_urls, desc="블로그 글 크롤링"):
        t, c, d = fetch_blog_content(url)
        if t or c:
            results.append({
                "url": url,
                "title": t,
                "content": c,
                "date": d
            })
        time.sleep(1)  # 요청 간 딜레이: 서버 부담 완화
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="네이버 블로그에서 IT 면접 관련 글 크롤링")
    parser.add_argument("--query", type=str, default="IT면접", help="검색할 키워드")
    parser.add_argument("--max-articles", type=int, default=10, help="최대 수집 글 개수")
    parser.add_argument("--output-path", type=str, default="blog_results.json", help="결과 저장 JSON 파일 경로")
    
    args = parser.parse_args()
    
    logger.info("네이버 블로그 검색 결과 크롤링 시작")
    blog_results = crawl_blog_search(args)
    logger.info(f"총 {len(blog_results)}개의 글 수집 완료")
    
    with open(args.output_path, "w", encoding="utf-8") as f:
        json.dump(blog_results, f, ensure_ascii=False, indent=4)
    
    logger.info(f"크롤링 결과가 {args.output_path}에 저장되었습니다.")