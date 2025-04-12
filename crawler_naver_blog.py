import argparse
import json
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tqdm import tqdm
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy

# Trafilatura 설정 (이 코드에서는 주로 BeautifulSoup 사용)
TRAFILATURA_CONFIG = deepcopy(DEFAULT_CONFIG)
TRAFILATURA_CONFIG["DEFAULT"]["DOWNLOAD_TIMEOUT"] = "5"
TRAFILATURA_CONFIG["DEFAULT"]["MAX_REDIRECTS"] = "0"
TRAFILATURA_CONFIG["DEFAULT"]["MIN_OUTPUT_SIZE"] = "50"


def fetch_blog_content(url: str) -> (str, str, str):
    """
    주어진 블로그 글 URL에서 모바일 페이지(m.blog.naver.com)를 대상으로
    제목, 본문, 작성일을 추출합니다.
    """
    try:
        # PC용 URL을 모바일 URL로 변환
        mobile_url = url.replace("://blog.naver.com/", "://m.blog.naver.com/")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/105.0.0.0 Safari/537.36"
        }
        resp = requests.get(mobile_url, headers=headers, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"블로그 페이지 요청 실패: {url} - {e}")
        return "", "", ""
    
    html = resp.text
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
    
    # 본문 추출: 새 에디터(.se-main-container) 또는 구 에디터(#postViewArea)
    content = ""
    main_container = soup.find("div", class_="se-main-container")
    if main_container:
        content = main_container.get_text(separator="\n", strip=True)
    else:
        post_view = soup.find("div", id="postViewArea")
        if post_view:
            content = post_view.get_text(separator="\n", strip=True)
    
    # 작성일 추출: meta 태그 또는 <span> 태그 내 YYYY.MM.DD 형식
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


def crawl_blog_urls(query: str, max_pages: int, max_articles: int) -> list:
    """
    네이버 검색 결과 URL의 page와 start 파라미터를 조작해 블로그 글 URL들을 수집합니다.
    - 한 페이지 당 15개의 결과가 있다고 가정하여, start = (page - 1) * 15 + 1로 계산합니다.
    - 검색 URL 예시:
      https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={encoded_query}&page={page}&start={start_val}
    - 블로그 글 링크는 상위 div 태그 "api_save_group _keep_wrap" 내부의 a 태그의 data-url 속성에 있습니다.
    """
    collected_urls = []
    encoded_query = quote(query)
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for page in range(1, max_pages + 1):
        start_val = (page - 1) * 15 + 1
        search_url = (
            f"https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={encoded_query}"
            f"&page={page}&start={start_val}"
        )
        try:
            resp = requests.get(search_url, headers=headers, timeout=5)
            resp.raise_for_status()
            logger.debug(f"검색 결과 HTML 일부 (페이지 {page}, start={start_val}): {resp.text[:300]}")
        except Exception as e:
            logger.error(f"검색 결과 요청 실패: {search_url} - {e}")
            continue
        
        soup = BeautifulSoup(resp.text, "html.parser")
        # 선택자: 상위 div 클래스가 "api_save_group _keep_wrap" 내부의 a 태그 (data-url 속성이 있음)
        posts = soup.select("div.api_save_group._keep_wrap a[data-url]")
        if not posts:
            logger.info(f"페이지 {page} (start={start_val})에서 결과를 찾지 못했습니다.")
            continue
        
        for post in posts:
            if len(collected_urls) >= max_articles:
                break
            url = post.get("data-url")
            if not url or url.strip() in {"#", "javascript:void(0)"} or not url.startswith("http"):
                continue
            if url not in collected_urls:
                collected_urls.append(url)
        logger.info(f"페이지 {page} (start={start_val}): 현재까지 {len(collected_urls)}개의 URL 수집됨")
        if len(collected_urls) >= max_articles:
            break
        time.sleep(1)
        
    return collected_urls


def crawl_blog_search(args) -> list:
    """
    수집한 URL 리스트를 바탕으로 각 블로그 글의 제목, 본문, 작성일을 크롤링합니다.
    """
    all_urls = crawl_blog_urls(args.query, args.max_pages, args.max_articles)
    results = []
    
    logger.info(f"총 {len(all_urls)}개의 URL 수집 완료. 개별 블로그 글 크롤링 시작")
    for url in tqdm(all_urls, desc="블로그 글 크롤링"):
        t, c, d = fetch_blog_content(url)
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
        description="네이버 블로그에서 IT 면접 관련 글 크롤링 (blog tab URL 사용, page 및 start 파라미터 조작)"
    )
    parser.add_argument("--query", type=str, default="IT면접", help="검색할 키워드")
    parser.add_argument("--max-pages", type=int, default=10, help="탐색할 최대 페이지 수 (한 페이지 당 15개 결과)")
    parser.add_argument("--max-articles", type=int, default=100, help="최대 수집 글 개수")
    parser.add_argument("--output-path", type=str, default="blog_results.json", help="결과 저장 JSON 파일 경로")
    
    args = parser.parse_args()
    
    blog_results = crawl_blog_search(args)
    
    with open(args.output_path, "w", encoding="utf-8") as f:
        json.dump(blog_results, f, ensure_ascii=False, indent=4)
    
    logger.info(f"크롤링 결과가 {args.output_path}에 저장되었습니다.")
