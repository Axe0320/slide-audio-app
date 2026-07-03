import asyncio
import datetime
import io
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path

import edge_tts
from flask import Flask, jsonify, request, send_file, send_from_directory

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)


def _resolve_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget_glob = list(Path.home().glob(
        "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe"
    ))
    if winget_glob:
        return str(winget_glob[0])
    return "ffmpeg"


FFMPEG_PATH = _resolve_ffmpeg()

DEFAULT_HEADING_KEYWORD = "Slide"

# in-memory job store: job_id -> {status, progress, total, files, error}
JOBS = {}

PREVIEW_DIR = BASE_DIR / "output" / ".preview"
PREVIEW_TEXT = {
    "ja-JP": "これはサンプルの音声です。速度とピッチはちょうど良いですか。\n\nここからは次の段落です。文と段落の間の一時停止も確認できます。",
    "en-US": "This is a sample of the selected voice. Does the speed and pitch sound right to you?\n\nThis is a new paragraph. You can also check the pause between sentences and paragraphs here.",
}


def build_heading_regex(keyword):
    escaped = re.escape(keyword.strip() or DEFAULT_HEADING_KEYWORD)
    return re.compile(rf"^\s*{escaped}\s*(\d+)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)


def parse_slides(text, keyword=DEFAULT_HEADING_KEYWORD):
    """Split raw script text into slides based on '<keyword> N: Title' headings."""
    heading_re = build_heading_regex(keyword)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    slides = []
    preface_lines = []
    current = None

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current is not None:
                slides.append(current)
            current = {
                "number": int(m.group(1)),
                "title": m.group(2).strip(),
                "body_lines": [],
            }
        else:
            if current is None:
                if line.strip():
                    preface_lines.append(line.strip())
            else:
                # Keep blank lines as "" markers so paragraph breaks (blank-line-separated
                # groups) survive the join below, instead of being silently discarded.
                current["body_lines"].append(line.strip())

    if current is not None:
        slides.append(current)

    clean_keyword = keyword.strip() or DEFAULT_HEADING_KEYWORD
    total = len(slides)
    result = []
    for i, s in enumerate(slides, start=1):
        body_lines = s["body_lines"]
        while body_lines and body_lines[0] == "":
            body_lines.pop(0)
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        result.append({
            "index": i,
            "title": f"{clean_keyword}{s['number']:02d} {s['title']}",
            "body": "\n".join(body_lines),
            "track": i,
            "total": total,
        })

    return {
        "preface": "\n".join(preface_lines).strip(),
        "slides": result,
    }


def paragraph_fallback_slides(text):
    """Used when no heading matches at all: treat each blank-line-separated
    paragraph as its own slide, numbered sequentially."""
    paragraphs = split_into_paragraphs(text)
    if not paragraphs:
        stripped = text.strip()
        paragraphs = [stripped] if stripped else []

    total = len(paragraphs)
    return [
        {
            "index": i,
            "title": f"テキスト{i:02d}",
            "body": para,
            "track": i,
            "total": total,
        }
        for i, para in enumerate(paragraphs, start=1)
    ]


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = name.strip().strip(".")
    return name or "untitled"


def synthesize_wav(text, wav_path, voice_name, rate_percent=0):
    """Use Windows SAPI (System.Speech) via PowerShell to render text to a WAV file."""
    escaped_text = text.replace("`", "``").replace('"', '`"').replace("$", "`$")
    escaped_path = str(wav_path).replace("`", "``").replace('"', '`"')
    escaped_voice = voice_name.replace("`", "``").replace('"', '`"')
    # SAPI Rate is an integer from -10 (slowest) to 10 (fastest); map our -50%..+100% slider onto it.
    sapi_rate = max(-10, min(10, round(rate_percent / 10)))

    script = f'''
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{ $synth.SelectVoice("{escaped_voice}") }} catch {{}}
$synth.Rate = {sapi_rate}
$synth.SetOutputToWaveFile("{escaped_path}")
$synth.Speak("{escaped_text}")
$synth.Dispose()
'''
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"TTS failed: {proc.stderr}")


def convert_to_mp3(wav_path, mp3_path, title, track, total, album):
    """Encode a WAV to MP3 (used for the SAPI engine, which outputs WAV)."""
    proc = subprocess.run(
        [
            FFMPEG_PATH, "-y", "-i", str(wav_path),
            "-codec:a", "libmp3lame", "-qscale:a", "2",
            "-metadata", f"title={title}",
            "-metadata", f"track={track}/{total}",
            "-metadata", f"album={album}",
            str(mp3_path),
        ],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr}")


def tag_mp3(src_mp3, dst_mp3, title, track, total, album):
    """Copy an already-encoded MP3 (e.g. from edge-tts) while embedding ID3 tags, no re-encode."""
    proc = subprocess.run(
        [
            FFMPEG_PATH, "-y", "-i", str(src_mp3), "-c", "copy",
            "-metadata", f"title={title}",
            "-metadata", f"track={track}/{total}",
            "-metadata", f"album={album}",
            str(dst_mp3),
        ],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr}")


def convert_to_wav(src_path, wav_path):
    proc = subprocess.run(
        [FFMPEG_PATH, "-y", "-i", str(src_path), str(wav_path)],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr}")


def _signed_str(value, unit):
    return f"{'+' if value >= 0 else ''}{value}{unit}"


def synthesize_edge_mp3(text, mp3_path, voice_name, rate_percent=0, pitch_hz=0):
    async def _run():
        communicate = edge_tts.Communicate(
            text, voice_name,
            rate=_signed_str(rate_percent, "%"),
            pitch=_signed_str(pitch_hz, "Hz"),
        )
        await communicate.save(str(mp3_path))
    asyncio.run(_run())


SENTENCE_SPLIT_RE = re.compile(r"[^.!?。！？]+[.!?。！？]*")


def split_into_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


DECIMAL_POINT_RE = re.compile(r"(?<=\d)\.(?=\d)")
DECIMAL_PLACEHOLDER = "\x00"


def split_into_sentences(paragraph):
    flat = re.sub(r"\s+", " ", paragraph).strip()
    # Protect decimal points (e.g. "0.9592") from being mistaken for sentence-ending periods.
    protected = DECIMAL_POINT_RE.sub(DECIMAL_PLACEHOLDER, flat)
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.findall(protected) if s.strip()]
    sentences = [s.replace(DECIMAL_PLACEHOLDER, ".") for s in sentences]
    return sentences or ([flat] if flat else [])


def concatenate_with_pauses(segment_paths, silence_durations, output_path):
    """Concatenate audio segments (mp3/wav), inserting a silent gap of
    silence_durations[i] seconds after segment_paths[i] (last entry has no trailing gap)."""
    inputs = []
    filter_parts = []
    concat_labels = []
    idx = 0

    def add_input_and_normalize(extra_args):
        nonlocal idx
        inputs.extend(extra_args)
        filter_parts.append(f"[{idx}:a]aformat=sample_rates=24000:channel_layouts=mono[a{idx}]")
        concat_labels.append(f"[a{idx}]")
        idx += 1

    for i, seg in enumerate(segment_paths):
        add_input_and_normalize(["-i", str(seg)])
        if i < len(silence_durations) and silence_durations[i] > 0:
            add_input_and_normalize(["-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={silence_durations[i]}"])

    filter_complex = ";".join(filter_parts) + ";" + "".join(concat_labels) + f"concat=n={len(concat_labels)}:v=0:a=1[out]"
    cmd = [FFMPEG_PATH, "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]",
           "-codec:a", "libmp3lame", "-qscale:a", "2", str(output_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {proc.stderr}")


def synthesize_slide_segmented(text, output_dir, base_name, engine, voice_name, fmt, title, track, total, album,
                                rate_percent, pitch_hz, sentence_pause, paragraph_pause):
    """Synthesize sentence-by-sentence and stitch the clips together with configurable
    silence gaps between sentences and between paragraphs."""
    paragraphs = split_into_paragraphs(text) or [text.strip() or title]
    tmp_dir = output_dir / f"__segs_{base_name}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        segment_paths = []
        silence_durations = []
        for p_idx, para in enumerate(paragraphs):
            sentences = split_into_sentences(para) or [para]
            for s_idx, sentence in enumerate(sentences):
                if engine == "edge":
                    seg_path = tmp_dir / f"seg_{p_idx}_{s_idx}.mp3"
                    synthesize_edge_mp3(sentence, seg_path, voice_name, rate_percent, pitch_hz)
                else:
                    seg_path = tmp_dir / f"seg_{p_idx}_{s_idx}.wav"
                    synthesize_wav(sentence, seg_path, voice_name, rate_percent)
                segment_paths.append(seg_path)

                is_last_in_para = s_idx == len(sentences) - 1
                is_last_para = p_idx == len(paragraphs) - 1
                if is_last_in_para and is_last_para:
                    continue
                silence_durations.append(paragraph_pause if is_last_in_para else sentence_pause)

        raw_output = tmp_dir / "concat_raw.mp3"
        concatenate_with_pauses(segment_paths, silence_durations, raw_output)

        if fmt == "mp3":
            final_path = output_dir / f"{base_name}.mp3"
            tag_mp3(raw_output, final_path, title, track, total, album)
        else:
            final_path = output_dir / f"{base_name}.wav"
            convert_to_wav(raw_output, final_path)
        return final_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def synthesize_slide(text, output_dir, base_name, engine, voice_name, fmt, title, track, total, album,
                      rate_percent=0, pitch_hz=0, sentence_pause=0, paragraph_pause=0):
    """Synthesize one slide's audio and return the final Path, honoring engine + format."""
    if sentence_pause > 0 or paragraph_pause > 0:
        return synthesize_slide_segmented(
            text, output_dir, base_name, engine, voice_name, fmt, title, track, total, album,
            rate_percent, pitch_hz, sentence_pause, paragraph_pause,
        )
    if engine == "edge":
        raw_mp3 = output_dir / f"{base_name}__raw.mp3"
        synthesize_edge_mp3(text, raw_mp3, voice_name, rate_percent, pitch_hz)
        try:
            if fmt == "mp3":
                final_path = output_dir / f"{base_name}.mp3"
                tag_mp3(raw_mp3, final_path, title, track, total, album)
            else:
                final_path = output_dir / f"{base_name}.wav"
                convert_to_wav(raw_mp3, final_path)
        finally:
            raw_mp3.unlink(missing_ok=True)
        return final_path
    else:
        wav_path = output_dir / f"{base_name}.wav"
        synthesize_wav(text, wav_path, voice_name, rate_percent)
        if fmt == "mp3":
            final_path = output_dir / f"{base_name}.mp3"
            convert_to_mp3(wav_path, final_path, title, track, total, album)
            wav_path.unlink(missing_ok=True)
            return final_path
        return wav_path


@app.after_request
def add_no_cache_headers(response):
    # This is a local dev tool under active iteration; never let the browser cache
    # the page or its static assets, so code changes always show up on reload.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    return send_from_directory(BASE_DIR / "templates", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR / "static", filename)


def _list_sapi_voices():
    script = '''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + "|" + $_.VoiceInfo.Culture }
'''
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True
    )
    voices = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "|" in line:
            name, culture = line.split("|", 1)
            voices.append({"name": name.strip(), "culture": culture.strip()})
    return voices


def _friendly_voice_name(short_name):
    # "en-US-AndrewNeural" -> "Andrew"; "ja-JP-NanamiNeural" -> "Nanami"
    part = short_name.split("-")[-1]
    return part[:-6] if part.endswith("Neural") else part


def _list_edge_voices():
    async def _fetch():
        return await edge_tts.list_voices()
    all_voices = asyncio.run(_fetch())
    relevant = [v for v in all_voices if v["Locale"] in ("en-US", "ja-JP")]
    return [
        {
            "name": v["ShortName"],
            "culture": v["Locale"],
            "gender": v["Gender"],
            "label": _friendly_voice_name(v["ShortName"]),
        }
        for v in relevant
    ]


@app.route("/api/voices")
def api_voices():
    engine = request.args.get("engine", "sapi")
    if engine == "edge":
        return jsonify({"voices": _list_edge_voices()})
    return jsonify({"voices": _list_sapi_voices()})


@app.route("/api/preview")
def api_preview():
    engine = request.args.get("engine", "sapi")
    voice_name = request.args.get("voice") or ("en-US-AndrewNeural" if engine == "edge" else "Microsoft Zira Desktop")
    lang = request.args.get("lang", "en-US")
    rate_percent = int(request.args.get("rate", 0) or 0)
    pitch_hz = int(request.args.get("pitch", 0) or 0)
    sentence_pause = float(request.args.get("sentence_pause", 0) or 0)
    paragraph_pause = float(request.args.get("paragraph_pause", 0) or 0)
    text = PREVIEW_TEXT.get(lang, PREVIEW_TEXT["en-US"])

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        final_path = synthesize_slide(
            text, PREVIEW_DIR, "preview", engine, voice_name, "mp3",
            "Preview", 1, 1, "Preview", rate_percent, pitch_hz, sentence_pause, paragraph_pause,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return send_file(final_path, mimetype="audio/mpeg", as_attachment=False)


@app.route("/api/pick-folder")
def api_pick_folder():
    # Uses the modern common file-open dialog (Explorer-style, shows files) in "pick a folder"
    # mode, instead of the legacy tree-only FolderBrowserDialog.
    script = '''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "保存先フォルダを選択（フォルダを開いた状態で「開く」を押してください）"
$dialog.ValidateNames = $false
$dialog.CheckFileExists = $false
$dialog.CheckPathExists = $true
$dialog.FileName = "このフォルダを選択"
$dialog.Filter = "フォルダ|`n"
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output (Split-Path $dialog.FileName -Parent)
}
'''
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-Command", script],
        capture_output=True, text=True
    )
    path = proc.stdout.strip()
    return jsonify({"path": path or None})


@app.route("/api/parse", methods=["POST"])
def api_parse():
    text = None
    source_name = None
    keyword = DEFAULT_HEADING_KEYWORD
    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        text = f.read().decode("utf-8", errors="replace")
        source_name = Path(f.filename).stem
        keyword = request.form.get("keyword") or DEFAULT_HEADING_KEYWORD
    else:
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        keyword = data.get("keyword") or DEFAULT_HEADING_KEYWORD

    if not text or not text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    parsed = parse_slides(text, keyword)
    if not parsed["slides"]:
        fallback = paragraph_fallback_slides(text)
        if fallback:
            parsed = {"preface": "", "slides": fallback}
    parsed["source_name"] = source_name
    return jsonify(parsed)


def _run_generation_job(job_id, slides, output_dir, engine, voice_name, fmt, album, rate_percent, pitch_hz,
                         sentence_pause, paragraph_pause):
    job = JOBS[job_id]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # `slides` is always the FULL list (kept for consistent numbering context);
    # only entries with selected=True are actually synthesized.
    to_generate = [s for s in slides if s.get("selected", True)]
    job["total"] = len(to_generate)

    try:
        for i, slide in enumerate(to_generate, start=1):
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return

            base_name = sanitize_filename(slide["title"])
            body = slide["body"]
            if not body.strip():
                body = slide["title"]

            track = slide.get("track", slide.get("index", i))
            total_tag = slide.get("total", len(slides))

            final_path = synthesize_slide(
                body, output_dir, base_name, engine, voice_name, fmt,
                slide["title"], track, total_tag, album, rate_percent, pitch_hz,
                sentence_pause, paragraph_pause,
            )

            job["files"].append({
                "index": slide["index"],
                "title": slide["title"],
                "filename": final_path.name,
                "path": str(final_path),
            })
            job["progress"] = i

        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    slides = data.get("slides", [])
    engine = data.get("engine", "sapi")
    voice_name = data.get("voice") or ("en-US-AndrewNeural" if engine == "edge" else "Microsoft Zira Desktop")
    fmt = data.get("format", "mp3")
    album = data.get("album") or "Slides"
    rate_percent = int(data.get("rate", 0) or 0)
    pitch_hz = int(data.get("pitch", 0) or 0)
    sentence_pause = float(data.get("sentence_pause", 0) or 0)
    paragraph_pause = float(data.get("paragraph_pause", 0) or 0)

    output_dir = data.get("output_dir")
    if output_dir:
        output_dir = str(output_dir)
    else:
        # No custom destination chosen: create a fresh, uniquely-named subfolder
        # per run under output/, instead of dumping every run into the same folder.
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_folder = sanitize_filename(f"{album}_{timestamp}")
        output_dir = str(DEFAULT_OUTPUT_DIR / run_folder)

    if not slides:
        return jsonify({"error": "スライドがありません"}), 400
    if not any(s.get("selected", True) for s in slides):
        return jsonify({"error": "生成対象のスライドが選択されていません"}), 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "running",
        "progress": 0,
        "total": len(slides),
        "files": [],
        "error": None,
        "output_dir": output_dir,
        "cancel_requested": False,
    }

    thread = threading.Thread(
        target=_run_generation_job,
        args=(job_id, slides, output_dir, engine, voice_name, fmt, album, rate_percent, pitch_hz,
              sentence_pause, paragraph_pause),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/cancel/<job_id>", methods=["POST"])
def api_cancel(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    if job["status"] == "running":
        job["cancel_requested"] = True
    return jsonify({"ok": True})


@app.route("/api/download-file")
def api_download_file():
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path required"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(p, as_attachment=True)


@app.route("/api/audio-file")
def api_audio_file():
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path required"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(p, as_attachment=False)


@app.route("/api/download-zip/<job_id>")
def api_download_zip(job_id):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "job not ready"}), 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in job["files"]:
            p = Path(f["path"])
            if p.exists():
                zf.write(p, arcname=f["filename"])
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="slide_audio.zip", mimetype="application/zip")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5678, debug=False)
