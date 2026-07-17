let currentSlides = [];
let currentJobId = null;

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.remove("hidden");
  });
});

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const dropzoneIcon = document.getElementById("dropzoneIcon");
const dropzoneText = document.getElementById("dropzoneText");
const dropzoneSub = document.getElementById("dropzoneSub");
const dropzoneFilename = document.getElementById("dropzoneFilename");

function showSelectedFile(name) {
  dropzone.classList.add("loaded");
  dropzoneIcon.textContent = "✅";
  dropzoneText.textContent = "読み込み完了";
  dropzoneSub.textContent = "別のファイルにする場合はここをクリックまたはドラッグ";
  dropzoneFilename.textContent = name;
  dropzoneFilename.classList.remove("hidden");
}

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) showSelectedFile(fileInput.files[0].name);
});

["dragenter", "dragover"].forEach(evt => {
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach(evt => {
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  });
});
dropzone.addEventListener("drop", e => {
  const files = e.dataTransfer.files;
  if (files.length) {
    fileInput.files = files;
    showSelectedFile(files[0].name);
  }
});

function getEngine() {
  return document.querySelector('input[name="engine"]:checked').value;
}

let voiceCache = [];

function getGenderLabel(g) {
  return g === "Male" ? "男性" : g === "Female" ? "女性" : "";
}

function applyVoiceFilters() {
  const lang = document.querySelector('input[name="voiceLang"]:checked').value;
  const gender = document.querySelector('input[name="voiceGender"]:checked').value;

  const filtered = voiceCache.filter(v => {
    if (v.culture !== lang) return false;
    if (gender !== "all" && v.gender && v.gender !== gender) return false;
    return true;
  });

  const select = document.getElementById("voiceSelect");
  select.innerHTML = "";
  filtered.forEach(v => {
    const opt = document.createElement("option");
    opt.value = v.name;
    const genderLabel = getGenderLabel(v.gender);
    opt.textContent = v.label ? `${v.label}${genderLabel ? "（" + genderLabel + "）" : ""}` : v.name;
    select.appendChild(opt);
  });
}

async function loadVoices() {
  const engine = getEngine();
  const select = document.getElementById("voiceSelect");
  select.innerHTML = "<option>読み込み中...</option>";

  const res = await fetch(`/api/voices?engine=${engine}`);
  const data = await res.json();
  voiceCache = data.voices;

  const hasGender = voiceCache.some(v => v.gender);
  document.getElementById("voiceGenderRow").classList.toggle("hidden", !hasGender);
  document.getElementById("pitchRow").classList.toggle("hidden", engine !== "edge");

  applyVoiceFilters();
}
loadVoices();

document.querySelectorAll('input[name="engine"]').forEach(el => {
  el.addEventListener("change", loadVoices);
});
document.querySelectorAll('input[name="voiceLang"], input[name="voiceGender"]').forEach(el => {
  el.addEventListener("change", applyVoiceFilters);
});

const rateSlider = document.getElementById("rateSlider");
const rateValue = document.getElementById("rateValue");
rateSlider.addEventListener("input", () => {
  rateValue.textContent = `${rateSlider.value >= 0 ? "+" : ""}${rateSlider.value}%`;
});

const pitchSlider = document.getElementById("pitchSlider");
const pitchValue = document.getElementById("pitchValue");
pitchSlider.addEventListener("input", () => {
  pitchValue.textContent = `${pitchSlider.value >= 0 ? "+" : ""}${pitchSlider.value}Hz`;
});

// Sliders use integer steps (0..N); the actual pause length in seconds is value/10.
const sentencePauseSlider = document.getElementById("sentencePauseSlider");
const sentencePauseValue = document.getElementById("sentencePauseValue");
sentencePauseSlider.addEventListener("input", () => {
  sentencePauseValue.textContent = `${(sentencePauseSlider.value / 10).toFixed(1)}秒`;
});

const paragraphPauseSlider = document.getElementById("paragraphPauseSlider");
const paragraphPauseValue = document.getElementById("paragraphPauseValue");
paragraphPauseSlider.addEventListener("input", () => {
  paragraphPauseValue.textContent = `${(paragraphPauseSlider.value / 10).toFixed(1)}秒`;
});

const volumeSlider = document.getElementById("volumeSlider");
const volumeValue = document.getElementById("volumeValue");
volumeSlider.addEventListener("input", () => {
  const v = parseInt(volumeSlider.value, 10);
  volumeValue.textContent = v === 0 ? "±0dB" : `${v > 0 ? "+" : ""}${v}dB`;
});

const previewBtn = document.getElementById("previewBtn");
const previewStatus = document.getElementById("previewStatus");
const previewAudio = document.getElementById("previewAudio");

previewBtn.addEventListener("click", () => {
  const engine = getEngine();
  const voice = document.getElementById("voiceSelect").value;
  const lang = document.querySelector('input[name="voiceLang"]:checked').value;
  const rate = rateSlider.value;
  const pitch = pitchSlider.value;
  const sentence_pause = sentencePauseSlider.value / 10;
  const paragraph_pause = paragraphPauseSlider.value / 10;
  const volume_db = parseInt(volumeSlider.value, 10);

  if (!voice) {
    previewStatus.textContent = "音声を選択してください";
    return;
  }

  previewBtn.disabled = true;
  previewStatus.textContent = "生成中...";

  const params = new URLSearchParams({ engine, voice, lang, rate, pitch, sentence_pause, paragraph_pause, volume_db });
  previewAudio.src = `/api/preview?${params.toString()}`;
  previewAudio.play().catch(() => {});
});

previewAudio.addEventListener("playing", () => {
  previewBtn.disabled = false;
  previewStatus.textContent = "再生中...";
});
previewAudio.addEventListener("ended", () => {
  previewStatus.textContent = "";
});
previewAudio.addEventListener("error", () => {
  previewBtn.disabled = false;
  previewStatus.textContent = "試聴の生成に失敗しました";
});

document.getElementById("pickFolderBtn").addEventListener("click", async () => {
  const res = await fetch("/api/pick-folder");
  const data = await res.json();
  if (data.path) {
    document.getElementById("outputDir").value = data.path;
  }
});

document.getElementById("keywordPreset").addEventListener("change", (e) => {
  document.getElementById("keywordCustom").classList.toggle("hidden", e.target.value !== "custom");
});

function getHeadingKeyword() {
  const preset = document.getElementById("keywordPreset").value;
  if (preset === "custom") {
    return document.getElementById("keywordCustom").value.trim() || "Slide";
  }
  return preset;
}

async function parseCurrentInput(keyword) {
  const activeTab = document.querySelector(".tab-btn.active").dataset.tab;
  let res;
  if (activeTab === "file") {
    const fileInput = document.getElementById("fileInput");
    if (!fileInput.files.length) {
      throw new Error("ファイルを選択してください");
    }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("keyword", keyword);
    res = await fetch("/api/parse", { method: "POST", body: formData });
  } else {
    const text = document.getElementById("pasteInput").value;
    res = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, keyword }),
    });
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "解析に失敗しました");
  return data;
}

const GENERIC_TITLE_RE = /^テキスト\d+$/;

function renumberSlides() {
  const total = currentSlides.length;
  currentSlides.forEach((s, i) => {
    s.index = i + 1;
    s.track = i + 1;
    s.total = total;
    // Only renumber the generic "テキストNN" placeholder title used by the
    // no-heading paragraph fallback; heading-derived titles (e.g. "Slide04 Intro")
    // encode the real slide number and must not be touched.
    if (GENERIC_TITLE_RE.test(s.title)) {
      s.title = `テキスト${String(i + 1).padStart(2, "0")}`;
    }
  });
}

document.getElementById("detectBtn").addEventListener("click", async () => {
  const errorBox = document.getElementById("parseError");
  errorBox.classList.add("hidden");
  try {
    const data = await parseCurrentInput(getHeadingKeyword());
    currentSlides = data.slides.map(s => ({ ...s, selected: true }));
    document.getElementById("albumInput").value = data.source_name || "Slides";
    renderSlides();
    document.getElementById("slidesSection").classList.remove("hidden");
  } catch (e) {
    errorBox.textContent = e.message;
    errorBox.classList.remove("hidden");
  }
});

document.getElementById("detectAppendBtn").addEventListener("click", async () => {
  const errorBox = document.getElementById("parseError");
  errorBox.classList.add("hidden");
  try {
    const data = await parseCurrentInput(getHeadingKeyword());
    const appended = data.slides.map(s => ({ ...s, selected: true }));
    currentSlides = currentSlides.concat(appended);
    renumberSlides();
    if (!document.getElementById("albumInput").value.trim()) {
      document.getElementById("albumInput").value = data.source_name || "Slides";
    }
    renderSlides();
    document.getElementById("slidesSection").classList.remove("hidden");
  } catch (e) {
    errorBox.textContent = e.message;
    errorBox.classList.remove("hidden");
  }
});

function renderSlides() {
  const list = document.getElementById("slidesList");
  list.innerHTML = "";
  currentSlides.forEach((slide, i) => {
    const row = document.createElement("div");
    row.className = "slide-row";
    row.innerHTML = `
      <input type="checkbox" class="selected-checkbox" ${slide.selected ? "checked" : ""} title="生成対象">
      <div class="slide-index">${i + 1}</div>
      <div class="slide-fields">
        <input type="text" class="title-input" value="${escapeHtml(slide.title)}">
        <textarea class="body-input" rows="2">${escapeHtml(slide.body)}</textarea>
        <div class="track-fields">
          <label>トラック番号</label>
          <input type="number" class="track-input" min="1" value="${slide.track}">
          <label>/ 総数</label>
          <input type="number" class="total-input" min="1" value="${slide.total}">
        </div>
      </div>
      <button class="remove-btn" title="削除">×</button>
    `;
    row.querySelector(".selected-checkbox").addEventListener("change", e => {
      currentSlides[i].selected = e.target.checked;
    });
    row.querySelector(".title-input").addEventListener("input", e => {
      currentSlides[i].title = e.target.value;
    });
    row.querySelector(".body-input").addEventListener("input", e => {
      currentSlides[i].body = e.target.value;
    });
    row.querySelector(".track-input").addEventListener("input", e => {
      currentSlides[i].track = parseInt(e.target.value, 10) || 1;
    });
    row.querySelector(".total-input").addEventListener("input", e => {
      currentSlides[i].total = parseInt(e.target.value, 10) || 1;
    });
    row.querySelector(".remove-btn").addEventListener("click", () => {
      currentSlides.splice(i, 1);
      reindexSlides();
      renderSlides();
    });
    list.appendChild(row);
  });
}

document.getElementById("selectAllBtn").addEventListener("click", () => {
  currentSlides.forEach(s => { s.selected = true; });
  renderSlides();
});

document.getElementById("selectNoneBtn").addEventListener("click", () => {
  currentSlides.forEach(s => { s.selected = false; });
  renderSlides();
});

function reindexSlides() {
  currentSlides.forEach((s, i) => {
    s.index = i + 1;
    if (GENERIC_TITLE_RE.test(s.title)) {
      s.title = `テキスト${String(i + 1).padStart(2, "0")}`;
    }
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

document.getElementById("addSlideBtn").addEventListener("click", () => {
  const nextIndex = currentSlides.length + 1;
  currentSlides.push({
    index: nextIndex,
    title: `Slide${String(nextIndex).padStart(2, "0")} New Slide`,
    body: "",
    selected: true,
    track: nextIndex,
    total: currentSlides.length + 1,
  });
  renderSlides();
});

document.getElementById("generateBtn").addEventListener("click", async () => {
  const outputDir = document.getElementById("outputDir").value.trim();
  const album = document.getElementById("albumInput").value.trim() || "Slides";
  const artist = document.getElementById("artistInput").value.trim();
  const engine = getEngine();
  const voice = document.getElementById("voiceSelect").value;
  const format = document.querySelector('input[name="format"]:checked').value;
  const rate = parseInt(rateSlider.value, 10);
  const pitch = parseInt(pitchSlider.value, 10);
  const sentence_pause = sentencePauseSlider.value / 10;
  const paragraph_pause = paragraphPauseSlider.value / 10;
  const volume_db = parseInt(volumeSlider.value, 10);

  const res = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      slides: currentSlides, output_dir: outputDir || null, engine, voice, format, album, artist, rate, pitch,
      sentence_pause, paragraph_pause, volume_db,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "生成の開始に失敗しました");
    return;
  }
  currentJobId = data.job_id;
  document.getElementById("progressSection").classList.remove("hidden");
  document.getElementById("resultsSection").classList.add("hidden");
  document.getElementById("cancelBtn").classList.remove("hidden");
  document.getElementById("cancelBtn").disabled = false;
  pollStatus();
});

document.getElementById("cancelBtn").addEventListener("click", async () => {
  if (!currentJobId) return;
  document.getElementById("cancelBtn").disabled = true;
  document.getElementById("progressText").textContent += "（停止中...）";
  await fetch(`/api/cancel/${currentJobId}`, { method: "POST" });
});

async function pollStatus() {
  const res = await fetch(`/api/status/${currentJobId}`);
  const job = await res.json();

  const pct = job.total ? Math.round((job.progress / job.total) * 100) : 0;
  document.getElementById("progressFill").style.width = pct + "%";
  document.getElementById("progressText").textContent = `${job.progress}/${job.total} 処理中...`;

  if (job.status === "running") {
    setTimeout(pollStatus, 500);
  } else if (job.status === "done") {
    document.getElementById("progressText").textContent = `完了 (${job.total}件)`;
    document.getElementById("cancelBtn").classList.add("hidden");
    renderResults(job.files);
    document.getElementById("resultsSection").classList.remove("hidden");
  } else if (job.status === "cancelled") {
    document.getElementById("progressText").textContent = `停止しました（${job.files.length}件は生成済み）`;
    document.getElementById("cancelBtn").classList.add("hidden");
    if (job.files.length) {
      renderResults(job.files);
      document.getElementById("resultsSection").classList.remove("hidden");
    }
  } else if (job.status === "error") {
    document.getElementById("progressText").textContent = "エラー: " + job.error;
    document.getElementById("cancelBtn").classList.add("hidden");
  }
}

function renderResults(files) {
  const list = document.getElementById("resultsList");
  list.innerHTML = "";
  files.forEach(f => {
    const row = document.createElement("div");
    row.className = "result-row";
    row.innerHTML = `
      <div class="title">${escapeHtml(f.index + ". " + f.title)}</div>
      <audio controls src="/api/audio-file?path=${encodeURIComponent(f.path)}"></audio>
      <a class="secondary-btn dl-btn" href="/api/download-file?path=${encodeURIComponent(f.path)}">DL</a>
    `;
    list.appendChild(row);
  });
}

document.getElementById("downloadZipBtn").addEventListener("click", () => {
  if (currentJobId) {
    window.location.href = `/api/download-zip/${currentJobId}`;
  }
});
