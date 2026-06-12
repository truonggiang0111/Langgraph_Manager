# Facebook Post Bridge Extension

Minimal Chrome/Brave extension for accumulating Facebook post candidates while you scroll.

## What it does

- Scans visible Facebook posts on the current tab
- Keeps an in-page cache of posts that have already rendered while you scroll
- Expands visible `See more / Xem thêm` blocks before extracting candidate text
- Prefers the cleanest available text source from the visible post/anchors
- Scores candidates with soft keyword groups
- Can probe a candidate by clicking `Comment` and reading the visible overlay text
- Can OCR a photo-only candidate directly from the visible image region
- Exposes:
  - candidate text
  - usable link when available
  - photo-only hint for OCR fallback
  - image-heavy hints
  - whether the post has a visible `Comment` trigger

## Install

1. Open `chrome://extensions` or `brave://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select this folder:

`tools/facebook_post_bridge_extension`

## Notes

- This extension only runs on `facebook.com`
- It does not depend on any third-party extension
- `Read via Comment` is a live probe:
  - click a candidate's visible `Comment`
  - try to read text from the opened overlay
  - try to recover a permalink-like URL from the overlay
- `Scan` prefers:
  - post/permalink/group links over photo/search wrapper links
  - single photo-only posts as OCR candidates
  - and drops posts that only expose multiple photo links
- `OCR Photo`:
  - scrolls the candidate into view
  - captures the visible image region
  - sends it to the local OCR endpoint
  - returns extracted text in the popup
- `Scan` returns:
  - cached posts accumulated from scrolling
  - current visible post count
- `Scroll + Scan`:
  - scrolls a few bursts
  - lets the page-side cache update
  - then returns accumulated candidates
- `Send Local` posts the extracted JSON to `http://127.0.0.1:3342/facebook-extension-extract`
- The local worker saves each payload into:
  - `data/browser_artifacts/*.json`
