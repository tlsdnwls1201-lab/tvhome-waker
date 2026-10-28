"""알람 직전에 지니TV를 깨우고, 켜지는 즉시 우리집 홈 알람 화면을 연다 (GitHub Actions에서 10분마다 실행).

폰 설정(비공개 tvhome-data/data.json)에서 요일별 알람을 읽고, 앞으로 LOOKAHEAD 안에
알람이 있으면 그 EARLY 전까지 기다렸다가 지니TV에 전원 키를 보낸다.
GitHub의 예약 실행은 몇 시간씩 빠지기도 해서, 알람 몇 시간 전부터 기다리고 그동안 설정을 다시 읽는다.
이미 켜져 있으면 누르지 않고 알람 시각에 연다 (전원 키는 켜짐/꺼짐을 바꾸므로).
지니TV가 켜질 때 KT 방송 화면이 앞을 차지하므로, 켜지자마자 tvhome://alarm 링크로
알람 화면(노래 → 버튼 → 브리핑)을 열어 방송 소리보다 노래가 먼저 나오게 한다.
보내는 곳은 지니TV(GENIE_HOST) 하나뿐이다.

두 곳에서 돈다:
- 이 저장소(10분마다 예약 실행): 예비. 예약 실행이 빠지기도 한다.
- 데이터 저장소(--request): 지니TV 앱이 알람 5분 전에 wake.json을 남기면 바로 돈다. 대기 중에도 앱 예약은 정확하다.
앱이 요청해 둔 알람이면 예비 쪽은 30초 늦게 확인만 해서 전원 키를 두 번 누르지 않는다.

필요한 비밀 값(Secrets): GENIE_CERT, GENIE_KEY (리모컨 인증서), DATA_TOKEN (데이터 읽기 토큰, 데이터 저장소에서는 필요 없음)
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
WAKE_URL = "https://api.github.com/repos/tlsdnwls1201-lab/tvhome-data/contents/wake.json"
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # datetime.weekday() 순서
LOOKAHEAD = dt.timedelta(hours=5, minutes=30)  # 예약 실행이 몇 시간씩 빠져도 한 번만 걸리면 되게 (작업 한도 6시간 안)
RECHECK = 600                         # 기다리는 동안 10분마다 설정을 다시 읽는다 (알람을 바꾸거나 끄면 따른다)
EARLY = dt.timedelta(seconds=15)      # 켜지는 데 걸리는 만큼 먼저 전원을 넣는다
APP = "com.woojin.tvhome"
GUARD = 25                            # 알람 화면을 연 뒤 이 시간(초) 동안 앞에 있는지 지킨다
BACKUP_DELAY = 30                     # 앱이 요청한 알람이면 예비 실행은 알람 이 시간(초) 뒤에 확인만 한다


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def load_json(url, env_file):
    """데이터 저장소의 파일. 데이터 저장소 안에서 돌 때는 받아 둔 파일(env_file 환경 변수)을 읽는다."""
    if os.environ.get(env_file):
        with open(os.environ[env_file], encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + os.environ["DATA_TOKEN"],
        "Accept": "application/vnd.github.raw",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def load_data():
    return load_json(DATA_URL, "DATA_FILE")


def requested_alarm():
    """지니TV 앱이 남긴 깨우기 요청(wake.json)의 알람 시각. 없거나 읽지 못하면 None."""
    try:
        return dt.datetime.fromisoformat(load_json(WAKE_URL, "WAKE_FILE")["alarm"])
    except Exception:
        return None


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


async def wake_and_ring(host, at, late=False):
    """지니TV를 켜고, 켜지는 즉시 알람 화면을 연다 (KT 방송 소리가 먼저 나지 않게).
    이미 켜져 있으면 알람 시각에 연다. late(알람이 지난 뒤 시작)인데 켜져 있으면 건너뛴다."""
    cert, key = cert_files()
    for attempt in range(4):
        try:
            r = AndroidTVRemote("TV Home Waker", cert, key, host)
            await r.async_connect()
            await asyncio.sleep(1)
            if r.is_on and late:
                # 늦게 시작했는데 이미 켜져 있으면 앞 실행이나 앱의 예약 알람이 울렸거나 보고 있는 중이다: 화면을 빼앗지 않는다
                log("알람 시각이 지났고 지니TV가 켜져 있어 건너뛰어요")
                r.disconnect()
                return True
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


def wait_for_alarm(at):
    """알람 EARLY 전까지 기다린다. 그동안 설정을 다시 읽어 바뀐 시각을 따르고, 알람이 없어지면 None."""
    shown = None
    while True:
        now = dt.datetime.now(KST)
        if not at or at - now > LOOKAHEAD:
            log("가까운 알람 없음", "(다음:", at.strftime("%m/%d %H:%M") if at else "없음", ")")
            return None
        wait = (at - EARLY - now).total_seconds()
        if wait <= 0:
            return at
        if at != shown:  # 처음과 시각이 바뀌었을 때만 남긴다
            log("알람", at.strftime("%m/%d %H:%M"), "→", int(wait), "초 뒤에 깨워요")
            shown = at
        if wait <= RECHECK:
            time.sleep(wait)
            return at
        time.sleep(RECHECK)
        try:
            at = next_alarm(load_data(), dt.datetime.now(KST))
        except Exception as e:  # 잠깐 못 읽으면 원래 시각대로 기다린다
            log("설정을 다시 읽지 못했어요:", repr(e))


def main():
    now = dt.datetime.now(KST)
    late = False
    if "--now" in sys.argv:  # 수동 시험: 바로 깨우기
        at = now + EARLY
    elif "--request" in sys.argv:  # 지니TV 앱이 알람 5분 전에 남긴 요청으로 시작했다
        at = requested_alarm()
        log("앱이 요청한 알람:", at.strftime("%m/%d %H:%M") if at else "없음")
        if not at or at < now - dt.timedelta(minutes=2):
            log("요청한 알람이 이미 지났어요")
            return
        at = wait_for_alarm(at)
        if not at:
            return
    else:
        at = wait_for_alarm(next_alarm(load_data(), now))
        if not at:
            return
        if requested_alarm() == at:
            # 앱의 요청으로 데이터 저장소의 실행이 깨운다: 같이 누르면 전원이 다시 꺼지므로 늦게 확인만 한다
            log("앱이 요청한 알람이라 다른 실행이 깨워요.", BACKUP_DELAY, "초 뒤에 확인만 해요")
            time.sleep(max(0, (at - dt.datetime.now(KST)).total_seconds()) + BACKUP_DELAY)
            late = True
    late = late or dt.datetime.now(KST) > at
    ok = asyncio.run(wake_and_ring(os.environ["GENIE_HOST"], at, late=late))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
