# 이터널 리턴 패치노트 디스코드 알림

새 패치노트가 올라오면 디스코드 채널에 자동으로 메시지를 보내줍니다.
(디스코드 **웹훅**을 써서 봇 토큰이나 봇 초대 없이 동작합니다.)

공식 홈페이지 패치노트는 내용을 정리해서 마크다운으로 보냅니다.
```
# 이리 12.7버전 업데이트 패치 내역
## 실험체
리오 - 기본 체력 930 -> 900**(너프)**
버니스 - 기본 공격력 37 -> 40**(버프)**
```
- 버프/너프는 개발자 코멘트("상향", "하향" 등)로 판단하고, 코멘트가 없으면 수치 변화로 판단합니다.
- 버그 수정은 항목 수만 표시하고, 전체 내용은 마지막 줄의 원문 링크로 볼 수 있습니다.
- 2000자 제한 때문에 길면 여러 메시지로 나눠 보냅니다.

## 1. 웹훅 주소 만들기
1. 알림 받을 디스코드 채널 → ⚙️ 채널 편집 → **연동(Integrations)** → **웹후크** → **새 웹후크**
2. **웹후크 URL 복사**

## 2. 설정
1. `config.example.json`을 복사해서 이름을 `config.json`으로 바꾸기
2. `webhook_url`에 복사한 주소 붙여넣기

| 항목 | 설명 |
|---|---|
| `sources` | 기본 `["official"]`(공식 홈페이지, 요약 전송). `"steam"`(Steam 영문 공지, 링크만)도 추가 가능하지만 같은 패치가 2번 올 수 있습니다 |
| `check_interval_minutes` | 확인 주기(분) |
| `mention` | 알림과 같이 보낼 멘션. 예: `"@everyone"`, `"<@&역할ID>"` |

> ⚠️ 웹훅 주소는 비밀번호처럼 다루세요. 아는 사람은 누구나 그 채널에 글을 쓸 수 있습니다.

## 3. 실행
```
pip install -r requirements.txt
python patch_notifier.py --test         # 패치노트가 제대로 잡히는지 확인 (전송 안 함)
python patch_notifier.py --send-latest  # 최신 패치노트 1개를 보내서 웹훅 테스트
python patch_notifier.py                # 계속 켜두고 감시 (또는 run.bat 더블클릭)
```
처음 실행하면 기존 글은 "이미 본 글"로 저장만 하고, 그 뒤로 **새로 올라온 글만** 보냅니다. (`state.json`에 기록)

## 4. 24시간 돌리기 (GitHub Actions, 무료)
PC를 꺼도 GitHub가 10분마다 대신 확인해 줍니다.

1. GitHub에 **공개(Public)** 저장소를 만들고 이 폴더를 올립니다. (`config.json`은 `.gitignore`로 제외됨)
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DISCORD_WEBHOOK_URL` / Secret: 웹훅 주소
3. (선택) 같은 화면의 **Variables** 탭
   - `PATCH_SOURCES`: `steam` 또는 `official,steam` (기본값 `official,steam`)
   - `DISCORD_MENTION`: `@everyone` 등
4. **Actions** 탭 → "이터널 리턴 패치노트 알림" → **Run workflow**로 한 번 실행
   - 첫 실행은 기존 글을 기록만 하고 메시지는 안 보냅니다. 이후 새 글부터 전송.
   - 로그에서 `[official] 가져오기 실패`가 계속 보이면 `PATCH_SOURCES`를 `steam`으로 설정하세요.

60일간 저장소 활동이 없으면 GitHub가 예약 실행을 끕니다. 이메일이 오면 Actions 탭에서 다시 켜면 됩니다.

## 문제 해결
- `--test`에서 `SSLEOFError`가 나오면 그 네트워크가 게임 사이트 접속을 막고 있는 겁니다
  (학교/회사망, 공유기 유해사이트 차단 등). 다른 네트워크나 서버에서 실행하세요.
- `official`에서 "패치노트를 찾지 못함"이 나오면 공식 홈페이지 구조가 바뀐 것이니 `steam`만 쓰세요.
