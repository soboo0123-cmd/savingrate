from playwright.sync_api import sync_playwright
import json
import sys
import os
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# KST(한국 시간) 설정 
KST = timezone(timedelta(hours=9))

DEPOSIT_URL = "https://mall.epostbank.go.kr/IPDGDI0000.do?gds_cd=200000100101"
SAVINGS_URL = "https://mall.epostbank.go.kr/IPDGDI0000.do?gds_cd=300000100101"

def send_change_email(old_data, new_data):
    # 1. GitHub Secrets 환경변수 불러오기
    gmail_user = os.environ.get('GMAIL_USER')
    gmail_password = os.environ.get('GMAIL_APP_PASS')
    recipient = os.environ.get('RECIPIENT_EMAIL')
    
    # 환경변수가 하나라도 없으면 실행 취소 (로컬 테스트 에러 방지)
    if not all([gmail_user, gmail_password, recipient]):
        print("⚠️ 메일 환경변수가 설정되지 않아 알림 발송을 건너뜁니다.")
        return

    # 2. 메일 제목 및 본문 구성 (HTML 표 형식)
    subject = f"🔔 [우체국예금] 금리 변동 알림 ({new_data['date']})"
    
    html_content = f"""
    <html>
    <body>
        <h2>우체국 예적금 금리가 변동되었습니다.</h2>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
            <tr style="background-color: #f2f2f2;">
                <th>상품</th>
                <th>변경 전 (어제)</th>
                <th>변경 후 (오늘)</th>
            </tr>
            <tr>
                <td>정기예금 (1년)</td>
                <td>{old_data.get('deposit_rate', '-')} %</td>
                <td><b>{new_data.get('deposit_rate', '-')} %</b></td>
            </tr>
            <tr>
                <td>정기적금 (1년)</td>
                <td>{old_data.get('savings_rate', '-')} %</td>
                <td><b>{new_data.get('savings_rate', '-')} %</b></td>
            </tr>
        </table>
    </body>
    </html>
    """

    # 3. 이메일 객체 조립
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"우체국 금리알림 <{gmail_user}>"  # 발신자 이름 커스텀 적용
    msg['To'] = recipient
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    # 4. Gmail SMTP 서버를 통한 발송
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.set_debuglevel(1)  # 디버그 모드 활성화 (통신 과정 상세 출력)
            server.starttls()
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
            print("✅ Gmail SMTP를 통한 변동 알림 메일 발송 성공!")
            
    except Exception as e:
        # 에러가 발생해도 전체 스크립트를 중단하지 않아 JSON 데이터는 정상 저장됨
        print(f"❌ 메일 발송 중 에러 발생: {e}")


def crawl_with_playwright():
    """Playwright를 사용하여 예금/적금 금리를 순차 크롤링합니다."""
    results = {}
    
    with sync_playwright() as p:
        try:
            print("🌐 Playwright 브라우저 시작...")
            headless_mode = True
            args = ["--ignore-certificate-errors"]
            
            if os.getenv('GITHUB_ACTIONS'):
                args.extend(["--no-sandbox", "--disable-setuid-sandbox"])
            
            browser = p.chromium.launch(headless=headless_mode, args=args)
            context = browser.new_context()
            page = context.new_page()

            # --- 1. 정기예금 크롤링 ---
            print("📡 정기예금 페이지 로드 중...")
            page.goto(DEPOSIT_URL, wait_until='networkidle')
            page.click("a:has-text('금리')")
            page.wait_for_timeout(2000)
            
            deposit_rate = None
            rows = page.query_selector_all("table tr")
            found_maturity = False
            for row in rows:
                texts = [cell.inner_text().strip() for cell in row.query_selector_all("td, th")]
                if len(texts) > 0 and '만기이자지급식' in texts[0]:
                    found_maturity = True
                    continue
                if found_maturity and len(texts) > 1 and '1년~1년3개월미만' in texts[0]:
                    deposit_rate = texts[1]
                    break
                if found_maturity and len(texts) > 0 and ('이자지급식' in texts[0] or '지급식' in texts[0]) and '만기이자지급식' not in texts[0]:
                    break
            
            if not deposit_rate or deposit_rate == '-':
                raise Exception("정기예금 금리를 찾을 수 없습니다.")
            print(f" ✅ 정기예금 크롤링 성공! 금리: {deposit_rate}%")
            results['deposit_rate'] = deposit_rate

            # --- 2. 정기적금 크롤링 ---
            print("📡 정기적금 페이지 로드 중...")
            page.goto(SAVINGS_URL, wait_until='networkidle')
            page.click("a:has-text('금리')")
            page.wait_for_timeout(2000)
            
            savings_rate = None
            rows = page.query_selector_all("table tr")
            found_maturity = False
            for row in rows:
                texts = [cell.inner_text().strip() for cell in row.query_selector_all("td, th")]
                if len(texts) > 1 and '만기이자지급식' in texts[1]:
                    found_maturity = True
                    continue
                if found_maturity and len(texts) > 1 and '1년~2년미만' in texts[0]:
                    savings_rate = texts[1]
                    break
                if found_maturity and '구분' in ' '.join(texts):
                    break
            
            if not savings_rate or savings_rate == '-':
                raise Exception("정기적금 금리를 찾을 수 없습니다.")
            print(f" ✅ 정기적금 크롤링 성공! 금리: {savings_rate}%")
            results['savings_rate'] = savings_rate

            browser.close()
            return results

        except Exception as e:
            print(f"❌ 크롤링 실패 (전체 중단): {str(e)}")
            if 'browser' in locals():
                browser.close()
            # 하나라도 실패하면 깃허브 액션에 에러 상태를 전달하기 위해 즉시 강제 종료
            sys.exit(1)

def main():
    print("=" * 70)
    print("우체국 정기예금/적금 통합 금리 크롤러")
    print("=" * 70)
    
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%Y-%m-%d")
    timestamp_str = now_kst.isoformat()

    # 1. 크롤링 실행
    scraped_data = crawl_with_playwright()
    
    # 2. 오늘 저장할 데이터셋 구성
    new_record = {
        "date": today_str,
        "deposit_rate": scraped_data['deposit_rate'],
        "savings_rate": scraped_data['savings_rate'],
        "timestamp": timestamp_str
    }

    # 3. 기존 JSON 파일 읽어오기 및 구조 초기화
    json_filename = 'rate_data.json'
    data = {"latest": {}, "history": []}
    
    if os.path.exists(json_filename):
        try:
            with open(json_filename, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                # 방어 로직: 읽어온 데이터가 딕셔너리이고, 'history' 키가 있을 때만 덮어씀
                if isinstance(loaded_data, dict) and "history" in loaded_data:
                    data = loaded_data
                else:
                    print("⚠️ 기존 JSON 파일의 구조가 구버전이어서 초기화 후 새로 덮어씁니다.")
        except json.JSONDecodeError:
            print("⚠️ JSON 파일이 깨져 있어 새로 생성합니다.")
            pass

    # 4. 금리 변동 비교 로직 (과거 latest 데이터가 존재할 경우)
    if data.get("latest") and "deposit_rate" in data["latest"]:
        old_record = data["latest"]
        if (old_record["deposit_rate"] != new_record["deposit_rate"] or 
            old_record["savings_rate"] != new_record["savings_rate"]):
            print("🔄 금리 변동이 감지되었습니다!")
            send_change_email(old_record, new_record)
        else:
            print("▶️ 금리 변동 없음.")

    # 5. History 리스트에 데이터 일괄 저장 (동일 날짜 덮어쓰기 로직 포함)
    date_exists_in_history = False
    for i, record in enumerate(data["history"]):
        if record["date"] == today_str:
            data["history"][i] = new_record
            date_exists_in_history = True
            print(f"ℹ️ {today_str} 데이터가 이미 존재하여 덮어쓰기 하였습니다.")
            break
            
    if not date_exists_in_history:
        data["history"].append(new_record)
        
    # 6. Latest 데이터 갱신
    data["latest"] = new_record

    # 7. 파일 저장
    with open(json_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print(f"\n💾 성공적으로 크롤링 및 저장을 완료했습니다. ({json_filename})")

if __name__ == "__main__":
    main()