"""Pure validation smoke checks inside the deployed bot. No Telegram sends or DB writes."""

import json
import subprocess
import sys

PROBE = r"""
import json
from copy import deepcopy
from app.ads_content import EMPTY,ContentLimitError,validate_content
from app.bot.ads import failure
from app.i18n import language,t
text='😀'*4096
entities=[{'type':'custom_emoji','offset':i*2,'length':2,'custom_emoji_id':str(531234567890+i)} for i in range(4096)]
content={**deepcopy(EMPTY),'text':text,'entities':entities}
assert validate_content(content)==content
caption={**deepcopy(EMPTY),'text':'😀'*1024,'media':[{'kind':'photo','file_id':'synthetic_validation_only'}]}
assert validate_content(caption)==caption
for media,limit,code in (([],4096,'ads_text_length'),(caption['media'],1024,'ads_caption_length')):
 try:validate_content({**deepcopy(EMPTY),'text':'😀'*(limit+1),'media':media})
 except ContentLimitError as e:
  assert (e.code,e.actual,e.limit)==(code,limit+1,limit)
  for lang in ('lt','en','ru'):
   token=language.set(lang)
   try:
    value=failure(e)
    assert str(limit+1) in value and str(limit) in value and value!=t('ads.error')
   finally:language.reset(token)
 else:raise AssertionError('Missing length guard')
print(json.dumps({'text_4096_characters':True,'custom_emoji_4096_preserved':True,'caption_1024_characters':True,'precise_lt_en_ru_errors':True,'telegram_messages_sent':0,'database_writes':0}))
"""


def main():
    expected = sys.argv[1]
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    row = json.loads(
        subprocess.check_output(docker + ["inspect", "safecheck-bot-1"], stderr=subprocess.DEVNULL)
    )[0]
    assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
    assert row["Config"]["Image"].endswith("-" + expected[:12])
    result = subprocess.run(
        docker + ["exec", "safecheck-bot-1", "python", "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert result.returncode == 0 and lines
    print("AdsContentVerification=" + json.dumps(json.loads(lines[-1]), sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("AdsContentVerification=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None
