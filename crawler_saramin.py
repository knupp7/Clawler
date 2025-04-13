import argparse
import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from loguru import logger
from tqdm import tqdm

# BASE_URL은 기존 URL 그대로 사용
BASE_URL = "https://www.saramin.co.kr/zf_user/interview-review"

def safe_get_text(element, default="", **kwargs):
    """요소가 있으면 텍스트를 반환하고, 없으면 기본값(default)을 반환합니다."""
    return element.get_text(**kwargs) if element else default

def parse_review(box) -> dict:
    """
    하나의 box_review 요소에서 필요한 정보를 추출하여 dict로 반환합니다.
    필수 요소가 누락되면 None을 반환하여 해당 리뷰는 건너뜁니다.
    """
    vt = box.find("div", class_="view_title")
    if vt is None:
        logger.warning("view_title 요소를 찾지 못했습니다. 해당 리뷰를 건너뜁니다.")
        return None

    # company: strong 태그 내부의 span 태그 내용 제거 후 텍스트 추출
    company_tag = vt.find("strong")
    if company_tag:
        span_tag = company_tag.find("span")
        if span_tag:
            span_tag.decompose()  # strong 내의 불필요한 span 제거
        company = safe_get_text(company_tag, default="").strip()
    else:
        company = ""
    
    # info_interview: ul 태그에서 추출
    info_interview = safe_get_text(vt.find("ul"), default="", strip=True, separator=" ")
    date = safe_get_text(vt.find("span", class_="txt_date"), default="").strip()
    
    vc = box.find("div", class_="view_cont")
    if vc is None:
        logger.warning("view_cont 요소를 찾지 못했습니다. 해당 리뷰를 건너뜁니다.")
        return None

    # info_emotion 영역 처리: review와 difficulty 추출
    overall = ""
    difficulty = ""
    result = ""
    ie = vc.find("div", class_="info_emotion")
    if ie:
        # review: 첫 번째 dl 태그의 dd 태그 텍스트
        dl_tags = ie.find_all("dl")
        if dl_tags and len(dl_tags) > 0 and dl_tags[0].find("dd"):
            overall = safe_get_text(dl_tags[0].find("dd"), default="").strip()
        # difficulty: class가 "spr_review"인 dd 태그의 텍스트 그대로 사용
        difficulty = safe_get_text(ie.find("dd", class_="spr_review"), default="").strip()
    
    # info_view 영역 처리
    ivs = vc.find_all("div", class_="info_view")
    interview_type = safe_get_text(ivs[0].find("ul"), default="").strip() if len(ivs) > 0 else ""
    num_interviewers = safe_get_text(ivs[1].find("ul"), default="").strip() if len(ivs) > 1 else ""
    process = safe_get_text(ivs[2].find("p", class_="txt_desc"), default="", strip=True, separator=" ") if len(ivs) > 2 else ""
    
    questions = []
    if len(ivs) > 3:
        q_ul = ivs[3].find("ul", class_="list_question")
        if q_ul:
            questions = [safe_get_text(li, default="").strip() for li in q_ul.find_all("li")]
    
    # tip 및 특이사항: vc 내의 p 태그(class="txt_desc")가 2개 이상이면 마지막 p 태그의 텍스트를 사용
    p_tags = vc.find_all("p", class_="txt_desc")
    tip = ""
    if len(p_tags) > 1:
        tip = safe_get_text(p_tags[-1], default="", strip=True, separator=" ")
    
    return {
        "company": company,
        "result": result,
        "date": date,
        "interview_info": info_interview,
        "overall_review": overall,
        "difficulty": difficulty,
        "interview_type": interview_type,
        "num_interviewers": num_interviewers,
        "process": process,
        "questions": questions,
        "tip": tip
    }

def fetch_reviews_page(page: int):
    """
    주어진 페이지의 HTML을 요청하고, div.box_review 요소 리스트를 반환합니다.
    """
    params = {
        "my": 0,
        "page": page,
        "csn": "",
        "group_cd": "",
        "orderby": "registration",
        "career_cd": "",
        "job_category": 2,
        "company_nm": ""
    }
    url = f"{BASE_URL}?{urlencode(params)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=5)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    boxes = soup.select("div.box_review")
    return boxes

def crawl_saramin_reviews(pages: int) -> list:
    all_reviews = []
    for page in range(1, pages + 1):
        logger.info(f"크롤링 중: 페이지 {page}")
        try:
            boxes = fetch_reviews_page(page)
        except Exception as e:
            logger.error(f"페이지 {page} 요청 에러: {e}")
            break
        if not boxes:
            logger.warning(f"페이지 {page}에서 리뷰를 찾지 못했습니다.")
            break
        for box in boxes:
            review = parse_review(box)
            if review is not None:
                all_reviews.append(review)
        time.sleep(1)  # 서버 부하 완화
    return all_reviews

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="사람인 인터뷰 리뷰 크롤러")
    parser.add_argument("--pages", type=int, default=1, help="크롤링할 페이지 수")
    parser.add_argument("--output", type=str, default="saramin_reviews.json", help="결과 저장 경로")
    args = parser.parse_args()

    logger.info("크롤링 시작")
    reviews = crawl_saramin_reviews(args.pages)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(reviews, f, ensure_ascii=False, indent=2)
    logger.info(f"크롤링 완료, 총 {len(reviews)}건 저장됨: {args.output}")
