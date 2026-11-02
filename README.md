# InstaBatch Downloader — Web UI

A modern dark-themed local web app for batch downloading Instagram Reels, posts, and videos with **highest quality extraction** using `yt-dlp`.

---

## 🚀 Features

- **Cell-by-cell URL input** — spreadsheet style with numbered rows, dynamic add/delete, and multi-line auto-paste.
- **Top Quality selector** — chooses the highest available resolution (`best`, `1080p`, `720p`, etc.) with combined best audio stream.
- **Cookie support** — import cookies directly from Chrome, Edge, Firefox, Brave, Safari, or Opera to bypass login blocks.
- **Parallel downloads** — configurable worker threads (1–20) for lightning-fast batch processing.
- **Live console & progress bar** — see resolution, codecs, file sizes, and download speeds in real time.
- **In-browser playback & direct save** — play downloaded videos directly or save them to any device from the web interface.
- **One-click Explorer access** — opens the `downloads/` folder directly from the UI.
- **Automatic launcher** — double-click `run.bat` and your browser opens automatically.

---

## ⚡ Quick Start

### Method 1: One-Click (Recommended)

1. Double-click **[`run.bat`](file:///c:/Users/rv367/insta%20download/run.bat)**
2. It sets up packages and launches your default browser at **http://localhost:5050**

### Method 2: Command Line

```powershell
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5050** in your browser.

---

## 💡 How to Use

1. **Enter Links**:
   - Type or paste an Instagram link in any row.
   - Or paste a block of links — each line will automatically populate its own cell.
   - Use `Enter` or `Tab` to jump to a new row.
2. **Quality & Cookies** *(under ⚙ Settings)*:
   - **Quality**: Defaults to **⭐ Best available (highest quality)**.
   - **Cookie source**: Select **Chrome** (or your browser) if Instagram asks for login.
   - **Parallel workers**: Set 5–10 for fast parallel downloads.
3. Click **⬇ Download Videos**.
4. View progress live in the log terminal.
5. In **📁 Downloaded Files**, click **▶ Play** to preview or **⬇ Save** to keep.

---

## 📁 Project Structure

```
insta download/
├── app.py                    ← Flask backend (REST API, multi-threaded downloader)
├── run.bat                   ← One-click batch launcher & auto browser open
├── requirements.txt          ← Python dependencies (flask, yt-dlp)
├── templates/
│   └── index.html            ← Responsive dark UI (cell-by-cell table, live logs)
└── downloads/                ← Stored video files (.mp4)
```
