#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_audio.py - מפיק קובצי אודיו לכל מילה במשחק "מילה אחרי מילה"
ומטמיע אותם בתוך קובץ ה-HTML (עובד בלי אינטרנט ובלי קול מותקן במכשיר).

שימוש:
  python build_audio.py --html word-by-word.html --engine edge   --out word-by-word.audio.html
  python build_audio.py --html word-by-word.html --engine azure   --out word-by-word.audio.html
  python build_audio.py --html word-by-word.html --engine folder --folder ./recordings --out ...

מנועים:
  edge    קול עצבי עברי (he-IL-HilaNeural / he-IL-AvriNeural) דרך הספרייה edge-tts.
          בלי מפתח. שימו לב: זה שירות לא רשמי של מיקרוסופט, ועלול להשתנות או להיחסם.
          התקנה: pip install edge-tts
  azure   Azure AI Speech הרשמי (REST). דורש מפתח:
          set AZURE_SPEECH_KEY=...   set AZURE_SPEECH_REGION=westeurope
  folder  הקלטות שלכם. קובץ לכל מילה בשם <מילה>.<סיומת> (mp3/m4a/wav/ogg/webm/opus),
          למשל כלב.m4a ו-לרוץ.mp3. הקלטה של משפט: משפט_<מילה>.<סיומת>, למשל משפט_כלב.m4a. אפשר להקליט בטלפון ולהעביר לתיקייה.

דרישות: python 3.8+ ו-ffmpeg בנתיב (PATH).

הגייה: מנועי הקול מקבלים את המילה עם ניקוד מלא (מתוך NIKUD בקובץ המשחק), וזה מפחית טעויות.
לתיקון נקודתי אפשר להשתמש בקובץ pronunciations.json (ליד הסקריפט) בפורמט {"מילה": "טקסט להקראה"};
ערך שם גובר על הניקוד.
עדיין לא מצליח? הקליטו את המילה בעצמכם ושימו בתיקיית ההקלטות (מנוע folder).
אפשר לשלב: --folder ./recordings גם עם edge/azure, ואז הקלטה שלכם גוברת על קול מלאכותי.
"""
import argparse, asyncio, base64, json, os, re, shutil, subprocess, sys, tempfile

EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".webm", ".opus", ".aac", ".flac")


def read_words(html):
    m = re.search(r"var RAW=\[(.*?)\n\];", html, re.S)
    if not m:
        sys.exit("לא נמצאה רשימת המילים (RAW) בקובץ ה-HTML")
    words = re.findall(r'^\["([^"]+)"', m.group(1), re.M)
    if not words:
        sys.exit("רשימת המילים ריקה")
    return words


def read_nikud(html):
    m = re.search(r"/\*NIKUD_START\*/\s*var NIKUD=(\{.*?\});\s*/\*NIKUD_END\*/", html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def read_sent(html):
    m = re.search(r"/\*SENT_START\*/\s*var SENT=(\{.*?\});\s*/\*SENT_END\*/", html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg לא נמצא. התקינו מ-https://ffmpeg.org והוסיפו ל-PATH")


def to_mp3(src, dst):
    """חיתוך שקט, איזון עוצמה, מונו, 24kbps: כ-3 עד 5 KB למילה."""
    af = ("silenceremove=start_periods=1:start_threshold=-45dB,"
          "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
          "loudnorm=I=-16:TP=-1.5:LRA=11,"
          "apad=pad_dur=0.05")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af,
           "-ac", "1", "-ar", "24000", "-codec:a", "libmp3lame", "-b:a", "24k", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")


def find_recording(folder, word):
    for ext in EXTS:
        p = os.path.join(folder, word + ext)
        if os.path.isfile(p):
            return p
    return None


# ---------- מנועים ----------
def synth_edge(items, voice, rate, tmpdir):
    try:
        import edge_tts
    except ImportError:
        sys.exit("חסרה הספרייה: pip install edge-tts")

    async def one(word, text, path):
        c = edge_tts.Communicate(text, voice, rate=rate)
        await c.save(path)

    async def run():
        out = {}
        for word, text in items:
            path = os.path.join(tmpdir, "e_%d.mp3" % len(out))
            try:
                await one(word, text, path)
                out[word] = path
                print("  ✓", word)
            except Exception as e:
                print("  ✗", word, "-", e)
        return out

    return asyncio.run(run())


def synth_azure(items, voice, rate, tmpdir):
    import urllib.request
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        sys.exit("הגדירו AZURE_SPEECH_KEY ו-AZURE_SPEECH_REGION")
    url = "https://%s.tts.speech.microsoft.com/cognitiveservices/v1" % region
    out = {}
    for word, text in items:
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ssml = ('<speak version="1.0" xml:lang="he-IL"><voice name="%s">'
                '<prosody rate="%s">%s</prosody></voice></speak>' % (voice, rate, esc))
        req = urllib.request.Request(url, data=ssml.encode("utf-8"), method="POST", headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "word-by-word-build"})
        path = os.path.join(tmpdir, "a_%d.mp3" % len(out))
        try:
            with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
                f.write(r.read())
            out[word] = path
            print("  ✓", word)
        except Exception as e:
            print("  ✗", word, "-", e)
    return out


def main():
    ap = argparse.ArgumentParser(description="הפקת אודיו ושילובו בקובץ HTML")
    ap.add_argument("--html", required=True, help="קובץ המשחק")
    ap.add_argument("--out", help="קובץ פלט (ברירת מחדל: <שם>.audio.html)")
    ap.add_argument("--engine", choices=["edge", "azure", "folder"], default="edge")
    ap.add_argument("--folder", help="תיקיית הקלטות אישיות (קודמות לקול מלאכותי)")
    ap.add_argument("--voice", default="he-IL-HilaNeural",
                    help="he-IL-HilaNeural (אישה) או he-IL-AvriNeural (גבר)")
    ap.add_argument("--rate", default="-15%", help="מהירות דיבור, למשל -15%% (איטי יותר)")
    ap.add_argument("--no-sentences", action="store_true",
                    help="לא להפיק אודיו למשפטים (ברירת מחדל: מפיק גם משפטים)")
    ap.add_argument("--keep-existing", action="store_true",
                    help="לא להפיק מחדש מילים שכבר יש להן אודיו בקובץ")
    a = ap.parse_args()

    check_ffmpeg()
    here = os.path.dirname(os.path.abspath(__file__))
    html = open(a.html, encoding="utf-8").read()
    words = read_words(html)
    print("נמצאו %d מילים" % len(words))

    nikud = read_nikud(html)
    if nikud:
        print("נטען ניקוד מלא ל-%d מילים (משמש להגייה במנועי הקול)" % len(nikud))
    prons = {}
    pp = os.path.join(here, "pronunciations.json")
    if os.path.isfile(pp):
        prons = {k: v for k, v in json.load(open(pp, encoding="utf-8")).items() if not k.startswith("_")}
        print("נטענו %d תיקוני הגייה" % len(prons))

    existing = {}
    if a.keep_existing:
        m = re.search(r"/\*AUDIO_START\*/\s*var AUDIO=(\{.*?\});\s*/\*AUDIO_END\*/", html, re.S)
        if m:
            try:
                existing = json.loads(m.group(1))
            except ValueError:
                pass

    sent = {} if a.no_sentences else read_sent(html)
    if sent:
        print("נמצאו %d משפטים" % len(sent))
    # רשימת פריטים: (מפתח באודיו, טקסט להקראה, שם קובץ הקלטה)
    items = [(w, prons.get(w) or nikud.get(w) or w, w) for w in words]
    items += [("S:" + w, sent[w].replace("[", "").replace("]", ""), "משפט_" + w) for w in words if w in sent]

    tmp = tempfile.mkdtemp(prefix="wbw_")
    raw = {}
    todo = []
    for key, text, recname in items:
        if key in existing:
            continue
        rec = find_recording(a.folder, recname) if a.folder else None
        if rec:
            raw[key] = rec
        elif a.engine != "folder":
            todo.append((key, text))

    if raw:
        print("הקלטות אישיות: %d" % len(raw))
    if todo:
        print("מפיק %d מילים במנוע %s..." % (len(todo), a.engine))
        fn = synth_edge if a.engine == "edge" else synth_azure
        raw.update(fn(todo, a.voice, a.rate, tmp))

    audio = dict(existing)
    total = 0
    for w, src in raw.items():
        dst = os.path.join(tmp, "o_%d.mp3" % len(audio))
        try:
            to_mp3(src, dst)
        except Exception as e:
            print("  ✗ המרה נכשלה:", w, "-", e)
            continue
        b = open(dst, "rb").read()
        total += len(b)
        audio[w] = "data:audio/mpeg;base64," + base64.b64encode(b).decode("ascii")

    missing = [w for w in words if w not in audio]
    missing += ["משפט:" + w for w in words if w in sent and ("S:" + w) not in audio]
    block = "/*AUDIO_START*/\nvar AUDIO=" + json.dumps(audio, ensure_ascii=False) + ";\n/*AUDIO_END*/"
    new_html, n = re.subn(r"/\*AUDIO_START\*/.*?/\*AUDIO_END\*/", lambda m: block, html, flags=re.S)
    if n != 1:
        sys.exit("לא נמצא אזור AUDIO בקובץ ה-HTML (גרסה ישנה?)")

    out = a.out or re.sub(r"\.html?$", "", a.html) + ".audio.html"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8").write(new_html)
    print("\nנכתב:", out)
    nw = len([k for k in audio if not k.startswith("S:")])
    ns = len(audio) - nw
    print("מילים עם אודיו: %d מתוך %d, משפטים: %d מתוך %d, כ-%d KB" % (nw, len(words), ns, len(sent), (total + 1023) // 1024))
    if missing:
        if len(missing) > 20:
            print("בלי אודיו: %d פריטים (ינוגנו בהקראת המכשיר)" % len(missing))
        else:
            print("בלי אודיו (ינוגנו בהקראת המכשיר):", " ".join(missing))


if __name__ == "__main__":
    main()
