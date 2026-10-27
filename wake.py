"""알람 직전에 지니TV를 깨우고, 켜지는 즉시 우리집 홈 알람 화면을 연다 (GitHub Actions에서 30분마다 실행).

폰 설정(비공개 tvhome-data/data.json)에서 요일별 알람을 읽고, 앞으로 LOOKAHEAD 안에
알람이 있으면 그 EARLY 전까지 기다렸다가 지니TV에 전원 키를 보낸다.
이미 켜져 있으면 누르지 않고 알람 시각에 연다 (전원 키는 켜짐/꺼짐을 바꾸므로).
지니TV가 켜질 때 KT 방송 화면이 앞을 차지하므로, 켜지자마자 tvhome://alarm 링크로
알람 화면(노래 → 버튼 → 브리핑)을 열어 방송 소리보다 노래가 먼저 나오게 한다.
보내는 곳은 지니TV(GENIE_HOST) 하나뿐이다.

필요한 비밀 값(Secrets): GENIE_CERT, GENIE_KEY (리모컨 인증서), DATA_TOKEN (데이터 읽기 토큰)
변수(Variables): GENIE_HOST (지니TV 공인 IP)
"""
import asyncio
import datetime as dt
import json
import os
import sys
import tempfile
import time
import urllib.request

from androidtvremote2 import AndroidTVRemote

KST = dt.timezone(dt.timedelta(hours=9))
DATA_URL = "https://api.github.com/repos/tlsdnwls1201-lab/tvhome-data/contents/data.json"
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # datetime.weekday() 순서
LOOKAHEAD = dt.timedelta(minutes=75)  # 실행이 30분마다라 늦게 시작해도 놓치지 않게 넉넉히
EARLY = dt.timedelta(seconds=15)      # 켜지는 데 걸리는 만큼 먼저 전원을 넣는다
APP = "com.woojin.tvhome"
GUARD = 25                            # 알람 화면을 연 뒤 이 시간(초) 동안 앞에 있는지 지킨다


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def load_data():
    req = urllib.request.Request(DATA_URL, headers={
        "Authorization": "Bearer " + os.environ["DATA_TOKEN"],
        "Accept": "application/vnd.github.raw",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def next_alarm(data, now):
    days = (data.get("alarm") or {}).get("days") or {}
    for i in range(8):
        day = (now + dt.timedelta(days=i)).date()
        cfg = days.get(DAY_KEYS[day.weekday()]) or {}
        if not cfg.get("on") or ":" not in str(cfg.get("time", "")):
            continue
        hh, mm = map(int, cfg["time"].split(":"))
        at = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=KST)
        if at > now - dt.timedelta(minutes=2):  # 방금 지난 알람도 (늦게 시작했을 때) 깨운다
            return at
    return None


def cert_files():
    d = tempfile.mkdtemp()
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    with open(cert, "w") as f:
        f.write(os.environ["GENIE_CERT"])
    with open(key, "w") as f:
        f.write(os.environ["GENIE_KEY"])
    return cert, key


async def keep_alarm_front(r):
    """알람 화면을 열고, 잠시 동안 KT 방송이 앞을 가리면 다시 연다 (앱은 3분 안의 두 번째 알람을 무시한다)."""
    r.send_launch_app_command("tvhome://alarm")
    log("알람 화면을 열었어요")
    sent = time.monotonic()
    end = sent + GUARD
    while time.monotonic() < end:
        await asyncio.sleep(0.5)
        if r.current_app != APP and time.monotonic() - sent > 3:
            r.send_launch_app_command("tvhome://alarm")
            sent = time.monotonic()
            log("다른 화면(", r.current_app, ")이 앞에 있어 알람 화면을 다시 열었어요")


async def wake_and_ring(host, at):
    """지니TV를 켜고, 켜지는 즉시 알람 화면을 연다 (KT 방송 소리가 먼저 나지 않게).
    이미 켜져 있으면 알람 시각에 연다."""
    cert, key = cert_files()
    for attempt in range(4):
        try:
            r = AndroidTVRemote("TV Home Waker", cert, key, host)
            await r.async_connect()
            await asyncio.sleep(1)
            if r.is_on:
                log("지니TV가 이미 켜져 있어요")
                await asyncio.sleep(max(0, (at - dt.datetime.now(KST)).total_seconds()))
            else:
                r.send_key_command("POWER")
                log("전원 키를 보냈어요")
                for _ in range(80):  # 최대 20초 동안 켜지는지 본다
                    await asyncio.sleep(0.25)
                    if r.is_on:
                        break
                if not r.is_on:
                    raise RuntimeError("켜졌다는 신호가 없어요")
                log("지니TV가 켜졌어요")
            await keep_alarm_front(r)
            r.disconnect()
            return True
        except Exception as e:  # 잠깐 연결이 안 될 수 있다
            log("시도", attempt + 1, "실패:", repr(e))
        await asyncio.sleep(5)
    return False


def main():
    now = dt.datetime.now(KST)
    at = next_alarm(load_data(), now)
    if "--now" in sys.argv:  # 수동 시험: 바로 깨우기
        at = now + EARLY
    if not at or at - now > LOOKAHEAD:
        log("가까운 알람 없음", "(다음:", at.strftime("%m/%d %H:%M") if at else "없음", ")")
        return
    wait = (at - EARLY - now).total_seconds()
    log("알람", at.strftime("%m/%d %H:%M"), "→", max(0, int(wait)), "초 뒤에 깨워요")
    if wait > 0:
        time.sleep(wait)
    ok = asyncio.run(wake_and_ring(os.environ["GENIE_HOST"], at))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
