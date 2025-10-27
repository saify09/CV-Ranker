from flask import Flask, request, jsonify, send_file, render_template_string
from werkzeug.utils import secure_filename
import os, re, time
from pdfminer.high_level import extract_text as pdf_extract_text
import docx
import pandas as pd
from datetime import datetime

# --------------------
# Config
# --------------------
UPLOAD_FOLDER = "uploads"
RESULTS_FOLDER = "results"
ALLOWED_EXTENSIONS = {"pdf", "docx"}
POLL_INTERVAL = 3  # seconds

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# --------------------
# Helpers
# --------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s-]{7,}\d")

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_file(path):
    if path.lower().endswith(".pdf"):
        try:
            return pdf_extract_text(path)
        except Exception as e:
            return f"[PDF read error: {e}]"
    elif path.lower().endswith(".docx"):
        try:
            doc = docx.Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return f"[DOCX read error: {e}]"
    return ""

def guess_name(text):
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+", line):
            return line
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return first[:80] if first else "Unknown"

def find_emails(text):
    return EMAIL_RE.findall(text)

def find_phones(text):
    return PHONE_RE.findall(text)

def score_text(text):
    keywords = {
        "python": 3,
        "machine learning": 4,
        "deep learning": 4,
        "ai": 3,
        "data science": 3,
        "opencv": 2,
        "django": 2,
        "flask": 2,
        "pytorch": 3,
        "tensorflow": 3,
        "sql": 2,
        "aws": 2,
        "docker": 2,
        "nlp": 3,
        "computer vision": 3,
        "object detection": 3
    }
    txt = text.lower()
    s = sum(txt.count(k) * w for k, w in keywords.items())
    if EMAIL_RE.search(text): s += 2
    if PHONE_RE.search(text): s += 1
    return int(s)


# --------------------
# Core Processing
# --------------------
def analyze_all_resumes():
    rows = []
    for fname in sorted(os.listdir(UPLOAD_FOLDER)):
        if not allowed_file(fname):
            continue
        fpath = os.path.join(UPLOAD_FOLDER, fname)
        text = extract_text_from_file(fpath)
        name = guess_name(text) or os.path.splitext(fname)[0]
        emails = find_emails(text)
        phones = find_phones(text)
        score = score_text(text)
        mtime = os.path.getmtime(fpath)
        rows.append({
            "filename": fname,
            "name": name,
            "emails": ", ".join(dict.fromkeys(emails)),
            "phones": ", ".join(dict.fromkeys(phones)),
            "score": score,
            "uploaded_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        })
    df = pd.DataFrame(rows)
    out_path = os.path.join(RESULTS_FOLDER, "ranked_resumes.xlsx")
    if df.empty:
        pd.DataFrame(columns=["Rank","filename","name","emails","phones","score","uploaded_at"]).to_excel(out_path, index=False)
        return [], out_path
    df = df.sort_values(by="score", ascending=False).reset_index(drop=True)
    df.index = df.index + 1
    df.insert(0, "Rank", df.index)
    df.to_excel(out_path, index=False)
    return df.to_dict(orient="records"), out_path


# --------------------
# Routes
# --------------------
@app.route("/")
def index():
    html = r'''
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Resume Ranker — Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://unpkg.com/dropzone@5/dist/min/dropzone.min.css" rel="stylesheet"/>
  <style>
    body { background:#f5f7fb; padding-top:30px; }
    .dz-message { text-align: center; font-size: 1.05rem; }
    .file-row { transition: background .2s; }
  </style>
</head>
<body>
<div class="container">
  <div class="row mb-4">
    <div class="col-md-8">
      <div class="card shadow-sm p-3">
        <h4 class="mb-3">Drag & Drop Resumes</h4>
        <form action="/upload" class="dropzone" id="resumeDropzone"></form>
        <small class="text-muted">Supports PDF and DOCX. Drop multiple files at once.</small>
      </div>
    </div>
    <div class="col-md-4">
      <div class="card shadow-sm p-3">
        <h5>Actions</h5>
        <p><button id="refreshBtn" class="btn btn-outline-primary btn-sm mb-2">Refresh Table</button></p>
        <p><a id="downloadXlsx" class="btn btn-success btn-sm mb-2" href="/download">Download Ranked Excel</a></p>
        <p><button id="deleteBtn" class="btn btn-outline-danger btn-sm">Delete All Data</button></p>
        <p class="text-muted small">Table auto-updates every {{poll}} seconds</p>
      </div>
    </div>
  </div>

  <div class="card shadow-sm">
    <div class="card-body">
      <h5 class="card-title">Processed Resumes (live)</h5>
      <div class="table-responsive" style="max-height:520px; overflow:auto;">
        <table class="table table-hover" id="resultsTable">
          <thead class="table-light sticky-top">
            <tr>
              <th>Rank</th>
              <th>Filename</th>
              <th>Name</th>
              <th>Emails</th>
              <th>Phones</th>
              <th>Score</th>
              <th>Uploaded At</th>
            </tr>
          </thead>
          <tbody id="tableBody">
          </tbody>
        </table>
      </div>
    </div>
  </div>
  <footer class="mt-3 text-center text-muted">Local demo • Files in <code>uploads/</code> • Excel in <code>results/</code></footer>
</div>

<script src="https://unpkg.com/dropzone@5/dist/min/dropzone.min.js"></script>
<script>
Dropzone.autoDiscover = false;
const pollInterval = {{poll}} * 1000;
const dz = new Dropzone("#resumeDropzone", {
  maxFilesize: 10,
  acceptedFiles: ".pdf,.docx",
  parallelUploads: 4,
  timeout: 60000,
  init: function() {
    this.on("queuecomplete", fetchData);
  }
});

document.getElementById("refreshBtn").addEventListener("click", fetchData);

document.getElementById("deleteBtn").addEventListener("click", async ()=>{
  if(confirm("⚠️ Are you sure you want to delete all uploaded resumes and clear the table?")){
    const resp = await fetch("/delete_all", {method:"POST"});
    if(resp.ok){ 
      alert("All resumes and Excel data deleted.");
      fetchData();
    } else {
      alert("Failed to delete files!");
    }
  }
});

async function fetchData(){
  try {
    const resp = await fetch("/data");
    if(!resp.ok) throw new Error("Bad response");
    const json = await resp.json();
    const rows = json.rows || [];
    const tbody = document.getElementById("tableBody");
    tbody.innerHTML = "";
    if(rows.length === 0){
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">No resumes uploaded yet.</td></tr>';
    } else {
      for(const r of rows){
        const tr = document.createElement("tr");
        tr.className = "file-row";
        tr.innerHTML = `
          <td>${r.Rank ?? ""}</td>
          <td>${escapeHtml(r.filename)}</td>
          <td>${escapeHtml(r.name)}</td>
          <td>${escapeHtml(r.emails)}</td>
          <td>${escapeHtml(r.phones)}</td>
          <td>${r.score}</td>
          <td>${escapeHtml(r.uploaded_at)}</td>
        `;
        tbody.appendChild(tr);
      }
    }
  } catch(err){
    console.error("Failed to fetch data:", err);
  }
}

function escapeHtml(s){ if(!s) return ""; return String(s).replace(/[&<>"']/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m])); }

fetchData();
setInterval(fetchData, pollInterval);
</script>
</body>
</html>
    '''
    return render_template_string(html, poll=POLL_INTERVAL)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return "No file", 400
    f = request.files["file"]
    if f.filename == "" or not allowed_file(f.filename):
        return "Bad file", 400
    filename = secure_filename(f.filename)
    dest = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        filename = f"{base}_{int(time.time())}{ext}"
        dest = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    f.save(dest)
    analyze_all_resumes()
    return ("", 204)


@app.route("/data")
def data():
    rows, _ = analyze_all_resumes()
    for r in rows:
        for k,v in r.items():
            if v is None: r[k] = ""
    return jsonify({"rows": rows})


@app.route("/download")
def download():
    _, path = analyze_all_resumes()
    return send_file(path, as_attachment=True)


@app.route("/delete_all", methods=["POST"])
def delete_all():
    # delete files and Excel
    for f in os.listdir(UPLOAD_FOLDER):
        os.remove(os.path.join(UPLOAD_FOLDER, f))
    for f in os.listdir(RESULTS_FOLDER):
        os.remove(os.path.join(RESULTS_FOLDER, f))
    analyze_all_resumes()  # recreate empty excel
    return jsonify({"status": "cleared"})


if __name__ == "__main__":
    app.run(debug=True)
