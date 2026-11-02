"""丸徳マーケットのInstagramストーリーズに営業時間の画像を自動投稿する。

GitHub Actions から平日15時・土日祝11時（日本時間）の少し前に呼ばれ、
投稿時刻まで待ってから投稿する。画像は images/ のJPEGを1日1枚、ファイル名順に使う。

使い方:
  python post_story.py --dry-run          今日〜2週間分の予定を表示するだけ（投稿しない）
  python post_story.py --slot weekday     平日枠として実行（今日が平日なら15時に投稿）
  python post_story.py --slot holiday     土日祝枠として実行（今日が土日祝なら11時に投稿）
  python post_story.py --now --force      今すぐ投稿（テスト用）
"""
import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import jpholiday
import requests

JST = ZoneInfo("Asia/Tokyo")
GRAPH = "https://graph.facebook.com/v23.0"
BASE_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BASE_DIR / "images"
LOG_FILE = BASE_DIR / "posts.log"

WEEKDAY_TIME = dt.time(15, 0)
HOLIDAY_TIME = dt.time(11, 0)
ROTATION_START = dt.date(2026, 1, 1)  # この日を1枚目として順番に回す
MAX_WAIT_SECONDS = 40 * 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # Instagramの上限


def is_holiday(day):
    return day.weekday() >= 5 or jpholiday.is_holiday(day)


def slot_for(day):
    return "holiday" if is_holiday(day) else "weekday"


def post_time_for(day):
    return HOLIDAY_TIME if is_holiday(day) else WEEKDAY_TIME


def list_images():
    images = sorted(p for p in IMAGES_DIR.glob("*") if p.suffix.lower() in (".jpg", ".jpeg"))
    if not images:
        sys.exit("images/ にJPEG画像がありません")
    return images


def pick_image(day, images):
    return images[(day - ROTATION_START).days % len(images)]


def image_url(image):
    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("GITHUB_SHA", "main")
    if not repo:
        return f"(GitHub上で実行したときのURL)/images/{image.name}"
    return f"https://raw.githubusercontent.com/{repo}/{ref}/images/{quote(image.name)}"


def already_posted(day):
    return LOG_FILE.exists() and f"{day.isoformat()} 投稿" in LOG_FILE.read_text(encoding="utf-8")


def log(message):
    stamp = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    line = f"{stamp} {message}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def check(response):
    if not response.ok:
        sys.exit(f"Instagram APIエラー ({response.status_code}): {response.text}")
    return response.json()


def publish_story(url, ig_user_id, token):
    container = check(requests.post(
        f"{GRAPH}/{ig_user_id}/media",
        data={"image_url": url, "media_type": "STORIES", "access_token": token},
        timeout=60,
    ))["id"]

    for _ in range(36):
        status = check(requests.get(
            f"{GRAPH}/{container}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )).get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"画像の取り込みに失敗しました (status={status})")
        time.sleep(5)
    else:
        sys.exit("画像の取り込みが3分以内に終わりませんでした")

    return check(requests.post(
        f"{GRAPH}/{ig_user_id}/media_publish",
        data={"creation_id": container, "access_token": token},
        timeout=60,
    ))["id"]


def dry_run(images):
    today = dt.datetime.now(JST).date()
    print(f"画像 {len(images)} 枚: " + ", ".join(p.name for p in images))
    for p in images:
        if p.stat().st_size > MAX_IMAGE_BYTES:
            print(f"  ※ {p.name} は8MBを超えています")
    print()
    for i in range(14):
        day = today + dt.timedelta(days=i)
        label = "土日祝" if is_holiday(day) else "平日"
        name = jpholiday.is_holiday_name(day)
        extra = f"（{name}）" if name else ""
        print(f"{day:%m/%d}({'月火水木金土日'[day.weekday()]}) {label}{extra} "
              f"{post_time_for(day):%H:%M}  {pick_image(day, images).name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=["weekday", "holiday"])
    parser.add_argument("--now", action="store_true", help="時刻・曜日を無視してすぐ投稿")
    parser.add_argument("--force", action="store_true", help="今日すでに投稿済みでも投稿")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    images = list_images()
    if args.dry_run:
        dry_run(images)
        return

    today = dt.datetime.now(JST).date()
    if not args.now:
        if args.slot != slot_for(today):
            print(f"今日は{'土日祝' if is_holiday(today) else '平日'}なので、この枠({args.slot})では投稿しません")
            return
        target = dt.datetime.combine(today, post_time_for(today), JST)
        wait = (target - dt.datetime.now(JST)).total_seconds()
        if 0 < wait <= MAX_WAIT_SECONDS:
            print(f"{target:%H:%M} まで {int(wait)} 秒待ちます")
            time.sleep(wait)

    if already_posted(today) and not args.force:
        print("今日はすでに投稿済みです")
        return

    ig_user_id = os.environ.get("IG_USER_ID")
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not ig_user_id or not token:
        sys.exit("IG_USER_ID と IG_ACCESS_TOKEN を設定してください")

    image = pick_image(today, images)
    media_id = publish_story(image_url(image), ig_user_id, token)
    log(f"{today.isoformat()} 投稿 {image.name} (media_id={media_id})")


if __name__ == "__main__":
    main()
